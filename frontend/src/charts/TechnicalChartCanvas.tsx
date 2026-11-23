import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';

export interface ChartOverlayProps {
  symbol: string;
  timeframe: string;
  candles: Array<{
    start_ts_ist: string;
    open: string;
    high: string;
    low: string;
    close: string;
    volume: number;
    is_closed: boolean;
  }>;
  indicators?: {
    ema_9?: string;
    ema_20?: string;
    ema_50?: string;
    ema_200?: string;
    vwap_session?: string;
    vwap?: string;
    rsi_14?: string;
    macd_line?: string;
    macd_signal?: string;
    macd_hist?: string;
    atr_14?: string;
    rvol?: string;
  };
  supportZone?: { lower: string; upper: string };
  resistanceZone?: { lower: string; upper: string };
  planLevels?: {
    entry?: string;
    stop?: string;
    initialStop?: string;
    target?: string;
    direction?: string;
  };
  depthPressure?: {
    buyQty?: number;
    sellQty?: number;
    pressure?: string;
  };
}

function toUtcTimestamp(iso: string, fallbackIndex: number): UTCTimestamp {
  const ms = Date.parse(iso);
  if (Number.isFinite(ms)) {
    return Math.floor(ms / 1000) as UTCTimestamp;
  }
  return (1700000000 + fallbackIndex * 900) as UTCTimestamp;
}

function tfToSeconds(tf: string): number {
  const map: Record<string, number> = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '2h': 7200,
    '3h': 10800,
    '4h': 14400,
    '1d': 86400,
    '1M': 2592000,
  };
  return map[tf] || 900;
}

function drawArrowHead(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  color: string,
) {
  const ang = Math.atan2(y2 - y1, x2 - x1);
  const len = 10;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - len * Math.cos(ang - Math.PI / 6), y2 - len * Math.sin(ang - Math.PI / 6));
  ctx.lineTo(x2 - len * Math.cos(ang + Math.PI / 6), y2 - len * Math.sin(ang + Math.PI / 6));
  ctx.closePath();
  ctx.fill();
}

function drawPillTag(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  bg: string,
  fg: string,
) {
  ctx.font = '700 10px Inter, system-ui, sans-serif';
  const tw = ctx.measureText(text).width;
  const pw = tw + 12;
  const ph = 18;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(x, y - ph / 2, pw, ph, 5);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.fillText(text, x + 6, y + 3.5);
}

/**
 * TRADEIQ NSE Pro — Smooth Pinch-Zoom/Pan Live Lightweight Chart + Exact Trade-Plan Mentor Overlay.
 * Fixes:
 * - Zero chart recreation on parent 1s timer ticks (uses primitive plan strings & candle signature).
 * - Full mobile pinch-zoom, drag-pan + explicit +, -, Fit, and Full Plan Scale toggle.
 * - Autoscales cleanly to include ENTRY, SL, and TARGET so trade zones & wave path are always visible.
 */
export const TechnicalChartCanvas: React.FC<ChartOverlayProps> = ({
  symbol,
  timeframe,
  candles,
  indicators,
  planLevels,
  depthPressure,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const pointsRef = useRef<
    Array<{ time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number }>
  >([]);

  // Extract primitive strings so parent re-renders never destroy/reset zoom!
  const entryStr = planLevels?.entry || '';
  const stopStr = planLevels?.stop || '';
  const initStopStr = planLevels?.initialStop || stopStr;
  const targetStr = planLevels?.target || '';
  const dirStr = (planLevels?.direction || '').toUpperCase();

  const [mentorEnabled, setMentorEnabled] = useState(true);
  const [fitPlanScale, setFitPlanScale] = useState(true);
  const [step, setStep] = useState<number>(4);
  const [allLayers, setAllLayers] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [countdownText, setCountdownText] = useState('--:--');
  const [hoverBar, setHoverBar] = useState<{
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  } | null>(null);

  // Keep latest state in a ref for the 60fps overlay loop without re-creating chart
  const overlayStateRef = useRef({
    mentorEnabled,
    step,
    allLayers,
    entryStr,
    stopStr,
    initStopStr,
    targetStr,
    dirStr,
  });
  overlayStateRef.current = {
    mentorEnabled,
    step,
    allLayers,
    entryStr,
    stopStr,
    initStopStr,
    targetStr,
    dirStr,
  };

  const latest = candles && candles.length > 0 ? candles[candles.length - 1] : null;
  const activeHud =
    hoverBar ||
    (latest
      ? {
          open: Number(latest.open),
          high: Number(latest.high),
          low: Number(latest.low),
          close: Number(latest.close),
          volume: Number(latest.volume || 0),
        }
      : null);

  // Candle dataset signature so chart only rebuilds when symbol, timeframe, or actual candles change
  const candleSig = useMemo(() => {
    if (!candles || candles.length === 0) return 'empty';
    const first = candles[0];
    const last = candles[candles.length - 1];
    return `${symbol}:${timeframe}:${candles.length}:${first.start_ts_ist}:${last.start_ts_ist}:${last.close}:${last.high}:${last.low}`;
  }, [symbol, timeframe, candles]);

  // Live candle close countdown timer
  useEffect(() => {
    const tick = () => {
      const sec = tfToSeconds(timeframe);
      const nowSec = Math.floor(Date.now() / 1000);
      const rem = sec - (nowSec % sec);
      if (rem >= 3600) {
        const h = Math.floor(rem / 3600);
        const m = Math.floor((rem % 3600) / 60);
        setCountdownText(`${String(h).padStart(2, '0')}h ${String(m).padStart(2, '0')}m`);
      } else {
        const m = Math.floor(rem / 60);
        const s = rem % 60;
        setCountdownText(`${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`);
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [timeframe]);

  // Auto-play Sir's walkthrough steps
  useEffect(() => {
    if (!playing) return;
    setMentorEnabled(true);
    setAllLayers(false);
    const id = window.setInterval(() => {
      setStep((prev) => (prev >= 5 ? 1 : prev + 1));
    }, 2600);
    return () => window.clearInterval(id);
  }, [playing]);

  // Zoom / Fit button helpers
  const handleZoom = (factor: number) => {
    const chart = chartRef.current;
    if (!chart) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    if (!range) return;
    const center = (range.from + range.to) / 2;
    const halfSpan = Math.max(4, ((range.to - range.from) / 2) * factor);
    chart.timeScale().setVisibleLogicalRange({
      from: center - halfSpan,
      to: center + halfSpan,
    });
  };

  const handleResetView = () => {
    chartRef.current?.timeScale().fitContent();
  };

  // Derive real-data Bengali Sir's Lesson
  const mentorSummary = useMemo(() => {
    if (!candles || candles.length < 3) {
      return {
        title: `স্যারের ক্লাস — ${symbol} (${timeframe})`,
        text: 'লাইভ ক্যান্ডেল লোড হচ্ছে — ক্যান্ডেল এলেই এন্ট্রি, SL ও টার্গেট প্ল্যান চার্টে দেখাবে।',
      };
    }
    const firstClose = Number(candles[Math.max(0, candles.length - 15)].close);
    const lastClose = Number(candles[candles.length - 1].close);
    const isShort = dirStr === 'SHORT' || (!dirStr && lastClose < firstClose);
    const vwap = Number(indicators?.vwap_session || indicators?.vwap || 0);
    const rsi = Number(indicators?.rsi_14 || 50);
    const entryP = Number(entryStr || lastClose);
    const slP = Number(stopStr || (isShort ? entryP * 1.015 : entryP * 0.985));
    const tpP = Number(targetStr || (isShort ? entryP * 0.97 : entryP * 1.03));
    const buyQ = depthPressure?.buyQty || 0;
    const sellQ = depthPressure?.sellQty || 0;
    const totalQ = Math.max(1, buyQ + sellQ);
    const buyPct = Math.round((buyQ / totalQ) * 100);
    const sellPct = 100 - buyPct;

    const map: Record<number, { title: string; text: string }> = {
      1: {
        title: `Step 1: লম্বা ট্রেন্ডলাইন ও স্ট্রাকচার (${symbol} • ${timeframe})`,
        text: isShort
          ? `ওপর থেকে নিচে টানা কমলা ট্রেন্ডলাইন দেখুন — ${symbol} নিম্নমুখী চাপে রয়েছে${vwap > 0 ? ` (VWAP ₹${vwap.toFixed(2)})` : ''}।`
          : `নিচ থেকে ওপরে টানা সবুজ ট্রেন্ডলাইন দেখুন — ${symbol} ঊর্ধ্বমুখী প্ল্যানে আছে${vwap > 0 ? ` (VWAP ₹${vwap.toFixed(2)})` : ''}।`,
      },
      2: {
        title: `Step 2: মূল সাপোর্ট / রেজিস্ট্যান্স ও জোন`,
        text: `হলুদ ড্যাশড জোনটি হলো নিকটবর্তী ব্রেকআউট রেঞ্জ। দাম এই জোন ধরে রাখলে বা ভাঙলে আসল মুভ শুরু হয়।`,
      },
      3: {
        title: `Step 3: ভলিউম সিগন্যাল ও 5-Level অর্ডার-বুক`,
        text: `হলুদ রিং দিয়ে সর্বোচ্চ ভলিউম ক্যান্ডেল চিহ্নিত করা হয়েছে। বর্তমান 5-Level ডেপথ: Buy ${buyPct}% বনাম Sell ${sellPct}% (RSI ${rsi.toFixed(1)})।`,
      },
      4: {
        title: `Step 4: আসল ট্রেড প্ল্যান — Entry, SL, Target ও ওয়েভ পথ`,
        text: `নীল লাইন Entry (₹${entryP.toFixed(2)}), লাল বক্স Risk/SL (₹${slP.toFixed(2)}), আর সবুজ বক্স Target (₹${tpP.toFixed(2)})। অ্যানিমেটেড নীল জিগজ্যাগ তীর দেখাচ্ছে দাম কীভাবে টার্গেটে পৌঁছাবে।`,
      },
      5: {
        title: `Step 5: SL সুরক্ষা ও লাইভ LTP ট্র্যাকিং`,
        text: `কমলা লাইনে বর্তমান SL (₹${slP.toFixed(2)}) সেট আছে। ১.৫R লাভে গেলে SL ব্রেকইভেনে শিফট হয়ে রিস্ক শূন্য করে দেবে।`,
      },
    };
    const chosen = map[step] || map[4];
    return {
      title: allLayers ? `সম্পূর্ণ ট্রেড প্ল্যান — ${symbol} (${timeframe})` : chosen.title,
      text: chosen.text,
    };
  }, [candles, indicators, entryStr, stopStr, targetStr, dirStr, depthPressure, step, allLayers, symbol, timeframe]);

  // Mount Lightweight Charts ONLY when candleSig, plan primitives, or fitPlanScale changes
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !candles || candles.length === 0) return;

    const entryNum = Number(entryStr);
    const stopNum = Number(stopStr);
    const targetNum = Number(targetStr);
    const hasPlan = Number.isFinite(entryNum) && entryNum > 0 && Number.isFinite(stopNum) && stopNum > 0 && Number.isFinite(targetNum) && targetNum > 0;

    const chart = createChart(el, {
      width: el.clientWidth || 720,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: '#040812' },
        textColor: '#94a3b8',
        fontFamily: 'Inter, system-ui, sans-serif',
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.42)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.42)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
        horzLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 10,
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
      autoscaleInfoProvider: hasPlan && fitPlanScale
        ? (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
            const res = original();
            if (!res || !res.priceRange) return res;
            const minV = Math.min(res.priceRange.minValue, entryNum, stopNum, targetNum);
            const maxV = Math.max(res.priceRange.maxValue, entryNum, stopNum, targetNum);
            const pad = (maxV - minV) * 0.04;
            return {
              priceRange: {
                minValue: minV - pad,
                maxValue: maxV + pad,
              },
            };
          }
        : undefined,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.84, bottom: 0 },
    });

    const seen = new Set<number>();
    const candlePoints: CandlestickData[] = [];
    const volPoints: HistogramData[] = [];
    const volByTime = new Map<number, number>();
    const parsedPts: Array<{
      time: UTCTimestamp;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
    }> = [];

    const sorted = [...candles]
      .map((c, idx) => ({ c, t: toUtcTimestamp(c.start_ts_ist, idx) }))
      .sort((a, b) => Number(a.t) - Number(b.t));

    for (const { c, t } of sorted) {
      let ts = Number(t);
      while (seen.has(ts)) ts += 60;
      seen.add(ts);
      const o = Number(c.open);
      const h = Number(c.high);
      const l = Number(c.low);
      const cl = Number(c.close);
      const v = Number(c.volume || 0);
      if (!Number.isFinite(o) || !Number.isFinite(cl)) continue;
      const ut = ts as UTCTimestamp;
      candlePoints.push({ time: ut, open: o, high: h, low: l, close: cl });
      volPoints.push({
        time: ut,
        value: v,
        color: cl >= o ? 'rgba(16, 185, 129, 0.34)' : 'rgba(244, 63, 94, 0.34)',
      });
      volByTime.set(ts, v);
      parsedPts.push({ time: ut, open: o, high: h, low: l, close: cl, volume: v });
    }

    candleSeries.setData(candlePoints);
    volumeSeries.setData(volPoints);
    pointsRef.current = parsedPts;
    chartRef.current = chart;
    seriesRef.current = candleSeries;

    // Clean trade plan price lines on right axis
    const addLine = (val: number, title: string, color: string, lineStyle = 0) => {
      if (Number.isFinite(val) && val > 0) {
        candleSeries.createPriceLine({
          price: val,
          color,
          lineWidth: 1,
          lineStyle,
          axisLabelVisible: true,
          title,
        });
      }
    };
    if (hasPlan) {
      addLine(entryNum, 'ENTRY', '#38bdf8', 0);
      addLine(stopNum, 'SL', '#f43f5e', 2);
      addLine(targetNum, 'TARGET', '#10b981', 2);
    }

    chart.timeScale().fitContent();

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setHoverBar(null);
        return;
      }
      const d = param.seriesData.get(candleSeries) as CandlestickData | undefined;
      if (d && typeof d.open === 'number') {
        setHoverBar({
          open: d.open,
          high: d.high,
          low: d.low,
          close: d.close,
          volume: volByTime.get(Number(param.time)) || 0,
        });
      }
    });

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
        }
      }
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chartRef.current = null;
      seriesRef.current = null;
      chart.remove();
    };
  }, [candleSig, entryStr, stopStr, targetStr, fitPlanScale]);

  // Synchronized 60fps Animated Mentor Overlay (reads refs, never recreates chart!)
  useEffect(() => {
    let rafId = 0;
    let phase = 0;

    const renderOverlay = () => {
      phase += 1;
      const canvas = overlayRef.current;
      const chart = chartRef.current;
      const series = seriesRef.current;
      const pts = pointsRef.current;
      const st = overlayStateRef.current;

      if (canvas && chart && series && pts.length >= 4) {
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        if (rect.width > 10 && rect.height > 10) {
          const targetW = Math.round(rect.width * dpr);
          const targetH = Math.round(rect.height * dpr);
          if (canvas.width !== targetW || canvas.height !== targetH) {
            canvas.width = targetW;
            canvas.height = targetH;
          }
          const ctx = canvas.getContext('2d');
          if (ctx) {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, rect.width, rect.height);

            const W = rect.width;
            const H = rect.height;
            const plotRight = W - 66;
            const plotBottom = H - 28;
            const tsApi = chart.timeScale();
            const show = (s: number) => st.mentorEnabled && (st.allLayers || st.step >= s);

            const n = pts.length;
            const last = pts[n - 1];
            const firstIdx = Math.max(0, n - 24);
            const windowPts = pts.slice(firstIdx);
            const isShort = st.dirStr === 'SHORT' || (!st.dirStr && last.close < windowPts[0].close);

            // Highest volume candle in recent window
            let volIdx = n - 1;
            let maxVol = -1;
            for (let i = Math.max(0, n - 18); i < n; i++) {
              if (pts[i].volume > maxVol) {
                maxVol = pts[i].volume;
                volIdx = i;
              }
            }

            // STEP 2: Key Structure / Consolidation Zone (only if step 2 or allLayers)
            if (show(2) && (st.step === 2 || st.allLayers)) {
              const zoneSlice = windowPts.slice(0, Math.max(3, Math.floor(windowPts.length * 0.45)));
              const zHi = Math.max(...zoneSlice.map((p) => p.high));
              const zLo = Math.min(...zoneSlice.map((p) => p.low));
              const yT = series.priceToCoordinate(zHi);
              const yB = series.priceToCoordinate(zLo);
              const xStart = tsApi.timeToCoordinate(zoneSlice[0].time) ?? 10;
              if (yT !== null && yB !== null) {
                const top = Math.max(6, Math.min(yT, yB));
                const bot = Math.min(plotBottom, Math.max(yT, yB));
                const h = Math.max(6, bot - top);
                const left = Math.max(6, xStart);
                ctx.fillStyle = 'rgba(251, 191, 36, 0.08)';
                ctx.fillRect(left, top, Math.max(20, plotRight - left), h);
                ctx.strokeStyle = 'rgba(251, 191, 36, 0.55)';
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(left, top, Math.max(20, plotRight - left), h);
                ctx.setLineDash([]);
                drawPillTag(ctx, left + 6, top + 12, 'STRUCTURE / ORB ZONE', 'rgba(251,191,36,0.22)', '#fde68a');
              }
            }

            // STEP 4: EXACT TRADE PLAN — ENTRY, SL (Red Box), TARGET (Green Box) + Wave Path
            if (show(4)) {
              const entryVal = Number(st.entryStr || last.close);
              const slVal = Number(st.stopStr || (isShort ? entryVal * 1.015 : entryVal * 0.985));
              const tpVal = Number(st.targetStr || (isShort ? entryVal * 0.97 : entryVal * 1.03));

              const rawYEnt = series.priceToCoordinate(entryVal);
              const rawYSl = series.priceToCoordinate(slVal);
              const rawYTp = series.priceToCoordinate(tpVal);
              const rawYLive = series.priceToCoordinate(last.close);

              const clampY = (y: number) => Math.max(12, Math.min(plotBottom - 8, y));
              const lastX = tsApi.timeToCoordinate(last.time) ?? plotRight * 0.65;
              const boxLeft = Math.max(12, Math.min(plotRight - 130, lastX - 40));
              const boxRight = plotRight - 4;

              if (rawYEnt !== null && rawYSl !== null && rawYTp !== null) {
                const yEnt = clampY(rawYEnt);
                const ySl = clampY(rawYSl);
                const yTp = clampY(rawYTp);

                // Red Risk Zone (Entry <-> SL)
                ctx.fillStyle = 'rgba(244, 63, 94, 0.14)';
                ctx.fillRect(boxLeft, Math.min(yEnt, ySl), boxRight - boxLeft, Math.max(2, Math.abs(ySl - yEnt)));
                // Green Profit Zone (Entry <-> Target)
                ctx.fillStyle = 'rgba(16, 185, 129, 0.14)';
                ctx.fillRect(boxLeft, Math.min(yEnt, yTp), boxRight - boxLeft, Math.max(2, Math.abs(yTp - yEnt)));

                // Explicit Crisp Pill Tags directly on Chart so Entry/SL/Target are ALWAYS clear!
                drawPillTag(ctx, boxLeft + 6, yEnt, `ENTRY ₹${entryVal.toFixed(2)}`, 'rgba(14,165,233,0.9)', '#04101d');
                drawPillTag(ctx, boxLeft + 6, ySl, `SL ₹${slVal.toFixed(2)}`, 'rgba(244,63,94,0.9)', '#ffffff');
                drawPillTag(ctx, boxLeft + 6, yTp, `TARGET ₹${tpVal.toFixed(2)}`, 'rgba(16,185,129,0.9)', '#04101d');

                // Animated Multi-Leg Zigzag Wave Path from Live Price -> Target
                const startX = Math.max(boxLeft + 24, Math.min(boxRight - 40, lastX));
                const startY = rawYLive !== null ? clampY(rawYLive) : yEnt;
                const dx = boxRight - 8 - startX;
                const dy = yTp - startY;
                if (dx > 18 && Math.abs(dy) > 10) {
                  const wPts: Array<[number, number]> = [
                    [startX, startY],
                    [startX + dx * 0.28, startY + dy * 0.42],
                    [startX + dx * 0.45, startY + dy * 0.26],
                    [startX + dx * 0.76, startY + dy * 0.78],
                    [boxRight - 8, yTp],
                  ];
                  ctx.save();
                  ctx.strokeStyle = '#22d3ee';
                  ctx.lineWidth = 2.4;
                  ctx.setLineDash([7, 5]);
                  ctx.lineDashOffset = -phase * 1.2;
                  ctx.beginPath();
                  wPts.forEach(([wx, wy], i) => (i === 0 ? ctx.moveTo(wx, wy) : ctx.lineTo(wx, wy)));
                  ctx.stroke();
                  ctx.setLineDash([]);
                  drawArrowHead(ctx, wPts[3][0], wPts[3][1], wPts[4][0], wPts[4][1], '#22d3ee');
                  ctx.restore();
                }
              }
            }

            // STEP 1: Diagonal Trendline (cleanly shown in Step 1 or All Layers)
            if (show(1) && (st.step === 1 || st.allLayers)) {
              const pStart = windowPts[0];
              const pEnd = windowPts[windowPts.length - 1];
              const xA = tsApi.timeToCoordinate(pStart.time);
              const xB = tsApi.timeToCoordinate(pEnd.time);
              const yA = series.priceToCoordinate(isShort ? pStart.high : pStart.low);
              const yB = series.priceToCoordinate(isShort ? pEnd.high : pEnd.low);
              if (xA !== null && xB !== null && yA !== null && yB !== null) {
                const col = isShort ? '#fb923c' : '#34d399';
                const extX = Math.min(plotRight - 8, xB + 22);
                const slope = (yB - yA) / Math.max(1, xB - xA);
                const extY = Math.max(14, Math.min(plotBottom - 10, yB + slope * (extX - xB)));
                ctx.strokeStyle = col;
                ctx.lineWidth = 2.4;
                ctx.beginPath();
                ctx.moveTo(xA, yA);
                ctx.lineTo(extX, extY);
                ctx.stroke();
                drawArrowHead(ctx, xA, yA, extX, extY, col);
                drawPillTag(
                  ctx,
                  Math.max(8, xA + 6),
                  Math.max(14, yA - 12),
                  isShort ? 'DOWN-TRENDLINE' : 'UP-TRENDLINE',
                  isShort ? 'rgba(251,146,60,0.22)' : 'rgba(52,211,153,0.22)',
                  isShort ? '#fdba74' : '#6ee7b7',
                );
              }
            }

            // STEP 3: Volume Surge Candle Halo (only Step 3 or All Layers)
            if (show(3) && (st.step === 3 || st.allLayers)) {
              const vp = pts[volIdx];
              const vx = tsApi.timeToCoordinate(vp.time);
              const vy = series.priceToCoordinate(vp.close);
              if (vx !== null && vy !== null) {
                const r = 13 + Math.sin(phase * 0.12) * 3;
                ctx.strokeStyle = '#facc15';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(vx, vy, r, 0, Math.PI * 2);
                ctx.stroke();
                drawPillTag(ctx, Math.max(8, vx - 42), Math.max(14, vy - 22), 'HIGH VOL SIGNAL', 'rgba(250,204,21,0.22)', '#fef08a');
              }
            }

            // Always-on Live Glowing LTP Tip
            const lx = tsApi.timeToCoordinate(last.time);
            const ly = series.priceToCoordinate(last.close);
            if (lx !== null && ly !== null && lx >= 0 && lx <= plotRight) {
              const glow = 6 + Math.sin(phase * 0.16) * 3;
              ctx.fillStyle = 'rgba(56, 189, 248, 0.25)';
              ctx.beginPath();
              ctx.arc(lx, ly, glow + 4, 0, Math.PI * 2);
              ctx.fill();
              ctx.fillStyle = '#38bdf8';
              ctx.beginPath();
              ctx.arc(lx, ly, 4, 0, Math.PI * 2);
              ctx.fill();
            }
          }
        }
      }
      rafId = window.requestAnimationFrame(renderOverlay);
    };

    rafId = window.requestAnimationFrame(renderOverlay);
    return () => window.cancelAnimationFrame(rafId);
  }, []);

  const vwapVal = indicators?.vwap_session || indicators?.vwap;

  return (
    <div className="space-y-3">
      {/* Pro OHLCV HUD Bar + Live Countdown */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-950/90 px-3 py-2.5 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-extrabold text-white">{symbol}</span>
          <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-mono text-sky-300">{timeframe}</span>
          <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[11px] font-mono text-amber-300">
            Close in {countdownText}
          </span>
        </div>
        {activeHud && (
          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]">
            <span className="text-slate-400">O <span className="text-white">₹{activeHud.open.toFixed(2)}</span></span>
            <span className="text-slate-400">H <span className="text-emerald-400">₹{activeHud.high.toFixed(2)}</span></span>
            <span className="text-slate-400">L <span className="text-rose-400">₹{activeHud.low.toFixed(2)}</span></span>
            <span className="text-slate-400">
              C <span className={activeHud.close >= activeHud.open ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>₹{activeHud.close.toFixed(2)}</span>
            </span>
            <span className="text-slate-400">Vol <span className="text-sky-300">{activeHud.volume.toLocaleString('en-IN')}</span></span>
          </div>
        )}
      </div>

      {/* Mentor + Zoom Toolbar (Mobile-Friendly 1-Tap Zoom + Plan Fit!) */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          {[
            { id: 1, label: '1. ট্রেন্ডলাইন' },
            { id: 2, label: '2. জোন' },
            { id: 3, label: '3. ভলিউম' },
            { id: 4, label: '4. ট্রেড প্ল্যান (R:R)' },
            { id: 5, label: '5. SL লক' },
          ].map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setPlaying(false);
                setMentorEnabled(true);
                setAllLayers(false);
                setStep(s.id);
              }}
              className="px-2.5 py-1.5 rounded-lg text-[11px] font-bold border transition"
              style={{
                backgroundColor: mentorEnabled && !allLayers && step === s.id ? 'rgba(56,189,248,0.22)' : '#0B1220',
                borderColor: mentorEnabled && !allLayers && step === s.id ? '#38bdf8' : '#1E293B',
                color: mentorEnabled && !allLayers && step === s.id ? '#E0F2FE' : '#94A3B8',
              }}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            type="button"
            onClick={() => handleZoom(0.75)}
            className="px-2.5 py-1.5 rounded-lg text-xs font-extrabold border border-slate-700 bg-slate-900 text-white"
            title="Zoom In"
          >
            ＋
          </button>
          <button
            type="button"
            onClick={() => handleZoom(1.35)}
            className="px-2.5 py-1.5 rounded-lg text-xs font-extrabold border border-slate-700 bg-slate-900 text-white"
            title="Zoom Out"
          >
            －
          </button>
          <button
            type="button"
            onClick={handleResetView}
            className="px-2.5 py-1.5 rounded-lg text-[11px] font-bold border border-slate-700 bg-slate-900 text-sky-300"
          >
            Fit
          </button>
          <button
            type="button"
            onClick={() => setFitPlanScale((f) => !f)}
            className="px-2.5 py-1.5 rounded-lg text-[11px] font-bold border"
            style={{
              backgroundColor: fitPlanScale ? 'rgba(16,185,129,0.18)' : '#0B1220',
              borderColor: fitPlanScale ? '#10B981' : '#1E293B',
              color: fitPlanScale ? '#6EE7B7' : '#94A3B8',
            }}
          >
            {fitPlanScale ? 'Full Plan: ON' : 'Candles Only'}
          </button>
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="px-3 py-1.5 rounded-lg text-[11px] font-extrabold transition"
            style={{ backgroundColor: playing ? '#F59E0B' : '#00E676', color: '#04120B' }}
          >
            {playing ? '⏸ পজ' : '▶ স্যারের ক্লাস'}
          </button>
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setMentorEnabled((m) => !m);
            }}
            className="px-2.5 py-1.5 rounded-lg text-[11px] font-bold border border-slate-800 bg-slate-950 text-slate-400"
          >
            {mentorEnabled ? 'ড্রয়িং লুকান' : 'ড্রয়িং দেখান'}
          </button>
        </div>
      </div>

      {/* TALL 500px TradingView Chart + Synchronized Mentor Overlay */}
      <div className="relative rounded-xl border border-slate-800 bg-[#040812] overflow-hidden">
        {candles && candles.length > 0 ? (
          <>
            <div ref={containerRef} className="w-full h-[500px]" style={{ touchAction: 'none' }} />
            <canvas
              ref={overlayRef}
              className="pointer-events-none absolute inset-0 w-full h-[500px] z-10"
            />
          </>
        ) : (
          <div className="h-[500px] flex items-center justify-center text-xs text-slate-400">
            Waiting for live Angel One OHLCV candles for {symbol} ({timeframe})...
          </div>
        )}
      </div>

      {/* Sir's Live Bengali Lesson Box */}
      {mentorEnabled && (
        <div
          className="rounded-xl p-3.5 border-l-4 space-y-1"
          style={{
            backgroundColor: '#0A101E',
            borderColor: '#38BDF8',
            borderTop: '1px solid #1E293B',
            borderRight: '1px solid #1E293B',
            borderBottom: '1px solid #1E293B',
          }}
        >
          <div className="text-xs font-extrabold text-sky-300">{mentorSummary.title}</div>
          <p className="text-xs text-slate-200 leading-relaxed">{mentorSummary.text}</p>
        </div>
      )}

      {/* Clean Indicator Strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs font-mono">
        <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
          <div className="text-[10px] font-sans text-slate-400">EMA 9 / 20 / 50</div>
          <div className="text-slate-200 mt-0.5">
            {indicators?.ema_9 ? Number(indicators.ema_9).toFixed(1) : '—'} / {indicators?.ema_20 ? Number(indicators.ema_20).toFixed(1) : '—'} / {indicators?.ema_50 ? Number(indicators.ema_50).toFixed(1) : '—'}
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
          <div className="text-[10px] font-sans text-slate-400">VWAP / ATR(14)</div>
          <div className="text-slate-200 mt-0.5">
            {vwapVal ? Number(vwapVal).toFixed(2) : '—'} • {indicators?.atr_14 ? Number(indicators.atr_14).toFixed(2) : '—'}
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
          <div className="text-[10px] font-sans text-slate-400">RSI(14)</div>
          <div className="text-sky-300 mt-0.5">{indicators?.rsi_14 ? Number(indicators.rsi_14).toFixed(2) : '—'}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
          <div className="text-[10px] font-sans text-slate-400">MACD Line / Signal</div>
          <div className="text-slate-200 mt-0.5">
            {indicators?.macd_line ? Number(indicators.macd_line).toFixed(2) : '—'} / {indicators?.macd_signal ? Number(indicators.macd_signal).toFixed(2) : '—'}
          </div>
        </div>
      </div>
    </div>
  );
};
