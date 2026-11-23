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

/**
 * TRADEIQ NSE Pro — Tall Live TradingView Lightweight Chart + Synchronized AI Trading Sir Overlay.
 * Supports 1m..1M timeframes, live countdown timer, and animated Step 1->5 mentor drawings
 * locked to real Lightweight Charts price/time coordinates.
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
  const pointsRef = useRef<Array<{ time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number }>>([]);

  const [mentorEnabled, setMentorEnabled] = useState(true);
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

  // Derive real-data trend & Sir's Bengali explanation
  const mentorSummary = useMemo(() => {
    if (!candles || candles.length < 3) {
      return {
        isShort: false,
        title: `স্যারের ক্লাস — ${symbol} (${timeframe})`,
        text: 'লাইভ ক্যান্ডেল লোড হচ্ছে — ক্যান্ডেল এলেই ট্রেন্ডলাইন, জোন ও রিস্ক-রিওয়ার্ড ড্রয়িং দেখাবে।',
      };
    }
    const firstClose = Number(candles[Math.max(0, candles.length - 15)].close);
    const lastClose = Number(candles[candles.length - 1].close);
    const dir = (planLevels?.direction || '').toUpperCase();
    const isShort = dir === 'SHORT' || (!dir && lastClose < firstClose);
    const vwap = Number(indicators?.vwap_session || indicators?.vwap || 0);
    const rsi = Number(indicators?.rsi_14 || 50);
    const entryP = Number(planLevels?.entry || lastClose);
    const slP = Number(planLevels?.stop || (isShort ? entryP * 1.015 : entryP * 0.985));
    const tpP = Number(planLevels?.target || (isShort ? entryP * 0.97 : entryP * 1.03));
    const buyQ = depthPressure?.buyQty || 0;
    const sellQ = depthPressure?.sellQty || 0;
    const totalQ = Math.max(1, buyQ + sellQ);
    const buyPct = Math.round((buyQ / totalQ) * 100);
    const sellPct = 100 - buyPct;

    const map: Record<number, { title: string; text: string }> = {
      1: {
        title: `Step 1: লম্বা ট্রেন্ডলাইন ও প্রাইস স্ট্রাকচার (${symbol} • ${timeframe})`,
        text: isShort
          ? `চার্টে ওপর থেকে নিচে টানা কমলা ট্রেন্ডলাইন দেখুন — ${symbol} এখন নিম্নমুখী চাপে আছে${vwap > 0 ? ` (VWAP ₹${vwap.toFixed(2)})` : ''}। বড় প্লেয়াররা ওপর থেকে সেল করছে।`
          : `চার্টে নিচ থেকে ওপরে টানা সবুজ ট্রেন্ডলাইন দেখুন — ${symbol} এখন ঊর্ধ্বমুখী ট্রেন্ডে আছে${vwap > 0 ? ` (VWAP ₹${vwap.toFixed(2)})` : ''}। বায়াররা দাম ধরে রাখছে।`,
      },
      2: {
        title: `Step 2: সাপোর্ট / রেজিস্ট্যান্স ও কনসোলিডেশন জোন`,
        text: `হলুদ শেডেড বক্সটি হলো গুরুত্বপূর্ণ রেঞ্জ জোন। দাম এই জোন ভাঙলে বা রিট্রেস্ট করলেই আসল মোমেন্টাম শুরু হয় — বক্সের ভেতরে অন্ধভাবে এন্ট্রি নেওয়া ঠিক নয়।`,
      },
      3: {
        title: `Step 3: ভলিউম স্পাইক ও 5-Level অর্ডার-বুক প্রেশার`,
        text: `হলুদ গ্লোয়িং রিং দিয়ে সবচেয়ে বড় ভলিউম ক্যান্ডেলটা চিহ্নিত করা হয়েছে। বর্তমান 5-Level ডেপথ অনুযায়ী Buy ${buyPct}% বনাম Sell ${sellPct}% (RSI: ${rsi.toFixed(1)})।`,
      },
      4: {
        title: `Step 4: জিগজ্যাগ ওয়েভ প্রজেকশন ও Risk/Reward বক্স`,
        text: `এন্ট্রি ₹${entryP.toFixed(2)} ধরে লাল বক্সে রিস্ক (SL ₹${slP.toFixed(2)}) এবং সবুজ বক্সে টার্গেট (₹${tpP.toFixed(2)}) আঁকা হয়েছে। অ্যানিমেটেড নীল জিগজ্যাগ তীর দেখাচ্ছে দাম ধাপে ধাপে কীভাবে টার্গেটের দিকে যেতে পারে।`,
      },
      5: {
        title: `Step 5: SL সুরক্ষা ও লাইভ এক্সিট প্ল্যান`,
        text: `কমলা ড্যাশড লাইন হলো বর্তমান অ্যাক্টিভ SL (₹${slP.toFixed(2)})। ট্রেড ১.৫R লাভে গেলেই SL ব্রেকইভেনে লক হয়ে রিস্ক শূন্য করে দেয়, আর ডানপাশের গ্লোয়িং ডট লাইভ LTP দেখাচ্ছে।`,
      },
    };

    const chosen = map[step] || map[4];
    return {
      isShort,
      title: allLayers ? `স্যারের সম্পূর্ণ ড্রয়িং — ${symbol} (${timeframe} • সব লেয়ার)` : chosen.title,
      text: chosen.text,
    };
  }, [candles, indicators, planLevels, depthPressure, step, allLayers, symbol, timeframe]);

  // Mount Lightweight Charts
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !candles || candles.length === 0) return;

    const chart = createChart(el, {
      width: el.clientWidth || 720,
      height: 520,
      layout: {
        background: { type: ColorType.Solid, color: '#040812' },
        textColor: '#94a3b8',
        fontFamily: 'Inter, system-ui, sans-serif',
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.45)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.45)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
        horzLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.22 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    const seen = new Set<number>();
    const candlePoints: CandlestickData[] = [];
    const volPoints: HistogramData[] = [];
    const volByTime = new Map<number, number>();
    const parsedPts: Array<{ time: UTCTimestamp; open: number; high: number; low: number; close: number; volume: number }> = [];

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
        color: cl >= o ? 'rgba(16, 185, 129, 0.35)' : 'rgba(244, 63, 94, 0.35)',
      });
      volByTime.set(ts, v);
      parsedPts.push({ time: ut, open: o, high: h, low: l, close: cl, volume: v });
    }

    candleSeries.setData(candlePoints);
    volumeSeries.setData(volPoints);
    pointsRef.current = parsedPts;
    chartRef.current = chart;
    seriesRef.current = candleSeries;

    // Clean trade plan lines only (keeps right price axis uncluttered!)
    const addLine = (valStr: string | undefined, title: string, color: string, lineStyle = 0) => {
      const p = Number(valStr);
      if (valStr && Number.isFinite(p) && p > 0) {
        candleSeries.createPriceLine({
          price: p,
          color,
          lineWidth: 1,
          lineStyle,
          axisLabelVisible: true,
          title,
        });
      }
    };
    addLine(planLevels?.entry, 'ENTRY', '#38bdf8', 0);
    addLine(planLevels?.stop, 'SL', '#f59e0b', 2);
    addLine(planLevels?.target, 'TARGET', '#10b981', 2);

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
  }, [candles, planLevels]);

  // Synchronized 60fps Animated Mentor Overlay locked to Lightweight Charts coordinates
  useEffect(() => {
    let rafId = 0;
    let phase = 0;

    const renderOverlay = () => {
      phase += 1;
      const canvas = overlayRef.current;
      const chart = chartRef.current;
      const series = seriesRef.current;
      const pts = pointsRef.current;

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
            const plotRight = W - 64;
            const tsApi = chart.timeScale();
            const show = (s: number) => mentorEnabled && (allLayers || step >= s);

            const n = pts.length;
            const last = pts[n - 1];
            const firstIdx = Math.max(0, n - 24);
            const windowPts = pts.slice(firstIdx);
            const dir = (planLevels?.direction || '').toUpperCase();
            const isShort = dir === 'SHORT' || (!dir && last.close < windowPts[0].close);

            // Find breakout / highest volume bar in recent window
            let volIdx = n - 1;
            let maxVol = -1;
            for (let i = Math.max(0, n - 18); i < n; i++) {
              if (pts[i].volume > maxVol) {
                maxVol = pts[i].volume;
                volIdx = i;
              }
            }

            // STEP 2: Shaded ORB / Range Zone
            if (show(2)) {
              const zoneSlice = windowPts.slice(0, Math.max(3, Math.floor(windowPts.length * 0.45)));
              const zHi = Math.max(...zoneSlice.map((p) => p.high));
              const zLo = Math.min(...zoneSlice.map((p) => p.low));
              const yT = series.priceToCoordinate(zHi);
              const yB = series.priceToCoordinate(zLo);
              const xStart = tsApi.timeToCoordinate(zoneSlice[0].time) ?? 10;
              if (yT !== null && yB !== null) {
                const top = Math.min(yT, yB);
                const h = Math.max(6, Math.abs(yB - yT));
                ctx.fillStyle = step === 2 ? 'rgba(251, 191, 36, 0.14)' : 'rgba(251, 191, 36, 0.06)';
                ctx.fillRect(Math.max(0, xStart), top, Math.max(20, plotRight - Math.max(0, xStart)), h);
                ctx.strokeStyle = 'rgba(251, 191, 36, 0.6)';
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(Math.max(0, xStart), top, Math.max(20, plotRight - Math.max(0, xStart)), h);
                ctx.setLineDash([]);
                ctx.fillStyle = '#fcd34d';
                ctx.font = '700 10px Inter, sans-serif';
                ctx.fillText('KEY STRUCTURE / ORB ZONE', Math.max(8, xStart + 8), top + 13);
              }
            }

            // STEP 4: Risk/Reward Shaded Box + Animated Multi-Leg Zigzag Wave Path
            if (show(4)) {
              const entryVal = Number(planLevels?.entry || last.close);
              const origSlVal = Number(planLevels?.initialStop || planLevels?.stop || (isShort ? entryVal * 1.015 : entryVal * 0.985));
              const tpVal = Number(planLevels?.target || (isShort ? entryVal * 0.97 : entryVal * 1.03));
              const yEnt = series.priceToCoordinate(entryVal);
              const ySl = series.priceToCoordinate(origSlVal);
              const yTp = series.priceToCoordinate(tpVal);
              const anchorX = tsApi.timeToCoordinate(pts[Math.max(0, volIdx)].time) ?? (plotRight * 0.55);
              const x1 = Math.max(12, Math.min(plotRight - 80, anchorX));
              const x2 = plotRight - 6;

              if (yEnt !== null && ySl !== null && yTp !== null) {
                // Red Risk Zone
                ctx.fillStyle = 'rgba(244, 63, 94, 0.13)';
                ctx.fillRect(x1, Math.min(yEnt, ySl), x2 - x1, Math.abs(ySl - yEnt));
                // Green Profit Zone
                ctx.fillStyle = 'rgba(16, 185, 129, 0.13)';
                ctx.fillRect(x1, Math.min(yEnt, yTp), x2 - x1, Math.abs(yTp - yEnt));

                // Animated Multi-Leg Zigzag Wave Projection Arrow
                const dx = x2 - x1;
                const dy = yTp - yEnt;
                const wPts: Array<[number, number]> = [
                  [x1, yEnt],
                  [x1 + dx * 0.24, yEnt + dy * 0.38],
                  [x1 + dx * 0.38, yEnt + dy * 0.22],
                  [x1 + dx * 0.68, yEnt + dy * 0.74],
                  [x1 + dx * 0.80, yEnt + dy * 0.58],
                  [x2 - 4, yTp],
                ];
                ctx.save();
                ctx.strokeStyle = '#22d3ee';
                ctx.lineWidth = 2.4;
                ctx.setLineDash([8, 6]);
                ctx.lineDashOffset = -phase * 1.25;
                ctx.beginPath();
                wPts.forEach(([wx, wy], i) => (i === 0 ? ctx.moveTo(wx, wy) : ctx.lineTo(wx, wy)));
                ctx.stroke();
                ctx.setLineDash([]);
                drawArrowHead(ctx, wPts[4][0], wPts[4][1], wPts[5][0], wPts[5][1], '#22d3ee');
                ctx.fillStyle = '#67e8f9';
                ctx.font = '700 10px Inter, sans-serif';
                ctx.fillText(
                  isShort ? 'ধাপে ধাপে নামার পথ (Wave Path)' : 'ধাপে ধাপে ওঠার পথ (Wave Path)',
                  wPts[2][0] - 10,
                  wPts[2][1] + (isShort ? -8 : 14),
                );
                ctx.restore();
              }
            }

            // STEP 1: Long Diagonal Teacher Trendline across Swings
            if (show(1)) {
              const pStart = windowPts[0];
              const pEnd = windowPts[windowPts.length - 1];
              const xA = tsApi.timeToCoordinate(pStart.time);
              const xB = tsApi.timeToCoordinate(pEnd.time);
              const yA = series.priceToCoordinate(isShort ? pStart.high : pStart.low);
              const yB = series.priceToCoordinate(isShort ? pEnd.high : pEnd.low);
              if (xA !== null && xB !== null && yA !== null && yB !== null) {
                const col = isShort ? '#fb923c' : '#34d399';
                const extX = Math.min(plotRight - 8, xB + 26);
                const slope = (yB - yA) / Math.max(1, xB - xA);
                const extY = yB + slope * (extX - xB);
                ctx.strokeStyle = col;
                ctx.lineWidth = step === 1 ? 2.6 : 1.9;
                ctx.beginPath();
                ctx.moveTo(xA, yA);
                ctx.lineTo(extX, extY);
                ctx.stroke();
                drawArrowHead(ctx, xA, yA, extX, extY, col);
                ctx.fillStyle = isShort ? '#fdba74' : '#6ee7b7';
                ctx.font = '700 10px Inter, sans-serif';
                ctx.fillText(isShort ? 'DOWN-TRENDLINE' : 'UP-TRENDLINE', Math.max(8, xA + 6), yA - 8);
              }
            }

            // STEP 3: Volume Surge Candle Halo
            if (show(3)) {
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
                ctx.fillStyle = '#fef08a';
                ctx.font = '700 10px Inter, sans-serif';
                ctx.fillText('HIGH VOL SIGNAL', Math.max(8, vx - 36), vy - 18);
              }
            }

            // STEP 5 / Always-On Live Glowing Price Tip
            const lx = tsApi.timeToCoordinate(last.time);
            const ly = series.priceToCoordinate(last.close);
            if (lx !== null && ly !== null) {
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
  }, [mentorEnabled, step, allLayers, planLevels]);

  const vwapVal = indicators?.vwap_session || indicators?.vwap;

  return (
    <div className="space-y-3">
      {/* Pro OHLCV HUD Bar + Live Countdown + Mentor Controls */}
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

      {/* Mentor Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          {[
            { id: 1, label: '1. ট্রেন্ডলাইন' },
            { id: 2, label: '2. জোন' },
            { id: 3, label: '3. ভলিউম' },
            { id: 4, label: '4. ওয়েভ ও R:R' },
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
        <div className="flex items-center gap-1.5">
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
              setMentorEnabled(true);
              setAllLayers((a) => !a);
            }}
            className="px-2.5 py-1.5 rounded-lg text-[11px] font-bold border"
            style={{
              backgroundColor: allLayers ? 'rgba(16,185,129,0.18)' : '#0B1220',
              borderColor: allLayers ? '#10B981' : '#1E293B',
              color: allLayers ? '#6EE7B7' : '#CBD5E1',
            }}
          >
            সব একসাথে
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

      {/* TALL 520px TradingView Chart + Synchronized Mentor Overlay */}
      <div className="relative rounded-xl border border-slate-800 bg-[#040812] overflow-hidden">
        {candles && candles.length > 0 ? (
          <>
            <div ref={containerRef} className="w-full h-[520px]" />
            <canvas
              ref={overlayRef}
              className="pointer-events-none absolute inset-0 w-full h-[520px] z-10"
            />
          </>
        ) : (
          <div className="h-[520px] flex items-center justify-center text-xs text-slate-400">
            Waiting for live Angel One OHLCV candles for {symbol} ({timeframe})...
          </div>
        )}
      </div>

      {/* Sir's Live Bengali Lesson Box */}
      {mentorEnabled && (
        <div
          className="rounded-xl p-3.5 border-l-4 space-y-1"
          style={{ backgroundColor: '#0A101E', borderColor: '#38BDF8', borderTop: '1px solid #1E293B', borderRight: '1px solid #1E293B', borderBottom: '1px solid #1E293B' }}
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
