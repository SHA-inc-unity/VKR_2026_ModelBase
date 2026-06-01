'use client';
import React from 'react';
import { Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';

// ── InfoTip ─────────────────────────────────────────────────────────────────

export function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center cursor-help text-muted-foreground/60 hover:text-muted-foreground">
          <Info className="w-3 h-3" />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  );
}

// ── Tiny helper components (kept inline to avoid extra files) ────────────────

export function ParamSection({
  title, enabled, onToggle, children, info,
}: {
  title: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children: React.ReactNode;
  info?: string;
}) {
  return (
    <div className={cn(
      'rounded-md border p-3 space-y-2',
      enabled ? 'border-border' : 'border-border opacity-60',
    )}>
      <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={e => onToggle(e.target.checked)}
        />
        {title}
        {info && <InfoTip text={info} />}
      </label>
      {enabled && <div className="pl-6 space-y-1.5">{children}</div>}
    </div>
  );
}

export function ParamRow({ label, children, info }: { label: string; children: React.ReactNode; info?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground w-32 flex-shrink-0 flex items-center gap-0.5">
        {label}{info && <InfoTip text={info} />}
      </span>
      {children}
    </div>
  );
}

export function NumInput({
  value, onChange, min, max, step,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={e => {
        const v = parseFloat(e.target.value);
        onChange(Number.isFinite(v) ? v : 0);
      }}
      className="h-8 w-28 rounded-md border bg-background px-2 text-xs"
    />
  );
}

export function NumField({
  label, value, onChange, min, max, step, width, info,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: string;
  info?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-muted-foreground flex items-center gap-0.5">{label}{info && <InfoTip text={info} />}</label>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => {
          const v = parseFloat(e.target.value);
          onChange(Number.isFinite(v) ? v : 0);
        }}
        className="h-9 rounded-md border bg-background px-2 text-sm"
        style={{ width }}
      />
    </div>
  );
}

export function Stat({
  label, value, accent,
}: {
  label: string;
  value: string;
  accent?: 'destructive' | 'warning';
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn(
        'text-lg font-bold tabular-nums',
        accent === 'destructive' && 'text-destructive',
        accent === 'warning'     && 'text-warning',
      )}>
        {value}
      </p>
    </div>
  );
}

export function CleanOpCard({
  checked, onCheck, label, count, children,
}: {
  checked: boolean;
  onCheck: (v: boolean) => void;
  label: string;
  count?: number;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn(
      'rounded-md border p-3 space-y-2',
      checked ? 'border-primary/40 bg-primary/5' : 'border-border',
    )}>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={e => onCheck(e.target.checked)}
        />
        <span className="flex-1">{label}</span>
        {count !== undefined && (
          <Badge variant="outline" className="tabular-nums">
            {count.toLocaleString()}
          </Badge>
        )}
      </label>
      {children}
    </div>
  );
}
