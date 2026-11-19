import React, { useEffect, useRef, useState } from 'react';
import {
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type HistogramData,
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
  planLevels?: { entry?: string; stop?: string; target?: string };
}

function toUtcTimestamp(iso: string, fallbackIndex: number): UTCTimestamp {
  const ms = Date.parse(iso);
  if (Number.isFinite(ms)) {
    return Math.floor(ms / 1000) as UTCTimestamp;
  }
  return (1700000000 + fallbackIndex * 900) as UTCTimestamp;
}

/**
 * Institutional TradingView Lightweight-Charts Candlestick + Volume + Indicator Canvas.
 * Supports smooth mouse-wheel zoom, drag pan, interactive crosshair OHLCV HUD,
 * and overlaid EMA / VWAP / Trade Plan price lines.
 */
export const TechnicalChartCanvas: React.FC<ChartOverlayProps> = ({
  symbol,
  timeframe,
  candles,
  indicators,
  planLevels,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [hoverBar, setHoverBar] = useState<{
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  } | null>(null);

  const latest = candles && candles.length > 0 ? candles[candles.length - 1] : null;
  const activeHud = hoverBar || (latest
    ? {
        open: Number(latest.open),
        high: Number(latest.high),
        low: Number(latest.low),
        close: Number(latest.close),
        volume: Number(latest.volume || 0),
      }
    : null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !candles || candles.length === 0) return;

    const chart = createChart(el, {
      width: el.clientWidth || 720,
      height: 360,
      layout: {
        background: { type: ColorType.Solid, color: '#020617' },
        textColor: '#94a3b8',
        fontFamily: 'Inter, system-ui, sans-serif',
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.55)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.55)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
        horzLine: { color: '#38bdf8', width: 1, style: 2, labelBackgroundColor: '#0f172a' },
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.08, bottom: 0.24 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
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
      scaleMargins: { top: 0.80, bottom: 0 },
    });

    // Deduplicate & sort strictly ascending by timestamp (required by lightweight-charts)
    const seen = new Set<number>();
    const candlePoints: CandlestickData[] = [];
    const volPoints: HistogramData[] = [];
    const volByTime = new Map<number, number>();

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
      candlePoints.push({ time: ts as UTCTimestamp, open: o, high: h, low: l, close: cl });
      volPoints.push({
        time: ts as UTCTimestamp,
        value: v,
        color: cl >= o ? 'rgba(16, 185, 129, 0.38)' : 'rgba(244, 63, 94, 0.38)',
      });
      volByTime.set(ts, v);
    }

    candleSeries.setData(candlePoints);
    volumeSeries.setData(volPoints);

    // Overlay key horizontal indicator/plan lines
    const addLine = (valStr: string | undefined, title: string, color: string, lineStyle = 2) => {
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

    addLine(indicators?.vwap_session || indicators?.vwap, 'VWAP', '#f59e0b', 0);
    addLine(indicators?.ema_20, 'EMA 20', '#38bdf8', 2);
    addLine(indicators?.ema_50, 'EMA 50', '#a78bfa', 2);
    addLine(planLevels?.entry, 'ENTRY', '#38bdf8', 0);
    addLine(planLevels?.stop, 'SL', '#f43f5e', 0);
    addLine(planLevels?.target, 'TARGET', '#10b981', 0);

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
      chart.remove();
    };
  }, [candles, indicators, planLevels]);

  const vwapVal = indicators?.vwap_session || indicators?.vwap;

  return (
    <div className="space-y-3">
      {/* Pro OHLCV HUD Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-950/90 px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white">{symbol}</span>
          <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-mono text-sky-300">{timeframe}</span>
          <span className="text-[11px] text-slate-400">TradingView Lightweight Chart • Scroll to Zoom, Drag to Pan</span>
        </div>
        {activeHud && (
          <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]">
            <span className="text-slate-400">O <span className="text-white">₹{activeHud.open.toFixed(2)}</span></span>
            <span className="text-slate-400">H <span className="text-emerald-400">₹{activeHud.high.toFixed(2)}</span></span>
            <span className="text-slate-400">L <span className="text-rose-400">₹{activeHud.low.toFixed(2)}</span></span>
            <span className="text-slate-400">C <span className={activeHud.close >= activeHud.open ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>₹{activeHud.close.toFixed(2)}</span></span>
            <span className="text-slate-400">Vol <span className="text-sky-300">{activeHud.volume.toLocaleString('en-IN')}</span></span>
          </div>
        )}
      </div>

      {/* TradingView Chart Mount */}
      <div className="rounded-xl border border-slate-800 bg-slate-950 overflow-hidden">
        {candles && candles.length > 0 ? (
          <div ref={containerRef} className="w-full h-[360px]" />
        ) : (
          <div className="h-[360px] flex items-center justify-center text-xs text-slate-400">
            Waiting for live Angel One OHLCV candles for {symbol} ({timeframe})...
          </div>
        )}
      </div>

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
          <div className="text-amber-300 mt-0.5">
            {vwapVal ? `₹${Number(vwapVal).toFixed(2)}` : '—'} • {indicators?.atr_14 ? Number(indicators.atr_14).toFixed(2) : '—'}
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
