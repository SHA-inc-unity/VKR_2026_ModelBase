'use client';

import { useState } from 'react';
import { Coins, Plus, X, RefreshCw, Loader2 } from 'lucide-react';
import { useCurrencyPairs, type PairAsset } from '@/hooks/useCurrencyPairs';
import { useToast } from '@/components/Toast';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';

function AssetColumn({
  title,
  hint,
  items,
  busy,
  onAdd,
  onRemove,
  onToggle,
}: {
  title: string;
  hint: string;
  items: PairAsset[];
  busy: boolean;
  onAdd: (asset: string) => void;
  onRemove: (asset: string) => void;
  onToggle: (asset: string, active: boolean) => void;
}) {
  const [value, setValue] = useState('');

  const submit = () => {
    const v = value.trim().toUpperCase();
    if (!v) return;
    onAdd(v);
    setValue('');
  };

  return (
    <Card className="flex-1 min-w-[260px]">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold flex items-center justify-between">
          <span>{title}</span>
          <Badge variant="secondary">{items.length}</Badge>
        </CardTitle>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardHeader>
      <Separator />
      <CardContent className="pt-4 space-y-3">
        <div className="flex gap-2">
          <Input
            value={value}
            onChange={e => setValue(e.target.value.toUpperCase())}
            onKeyDown={e => { if (e.key === 'Enter') submit(); }}
            placeholder="e.g. BTC"
            className="flex-1 font-mono"
          />
          <Button onClick={submit} disabled={busy || !value.trim()} className="gap-1">
            <Plus className="w-3.5 h-3.5" /> Add
          </Button>
        </div>
        <div className="flex flex-wrap gap-2">
          {items.length === 0 ? (
            <span className="text-xs text-muted-foreground">No assets yet</span>
          ) : (
            items.map(a => (
              <span
                key={a.asset}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-mono',
                  a.active
                    ? 'border-border bg-muted'
                    : 'border-dashed border-border/60 text-muted-foreground line-through',
                )}
              >
                <button
                  onClick={() => onToggle(a.asset, !a.active)}
                  disabled={busy}
                  title={a.active ? 'Click to disable' : 'Click to enable'}
                  className="font-semibold hover:text-primary disabled:opacity-50"
                >
                  {a.asset}
                </button>
                <button
                  onClick={() => onRemove(a.asset)}
                  disabled={busy}
                  title="Remove"
                  className="text-muted-foreground hover:text-destructive disabled:opacity-50"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function CurrencyPairsPage() {
  const { toast } = useToast();
  const { bases, quotes, symbols, loading, error, refresh, addAsset, removeAsset, setActive } = useCurrencyPairs();
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast(ok, 'success');
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Coins className="w-6 h-6" /> Currency Pairs
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Single source of truth — active pairs = active base × active quote. The dataset loader,
            Market Watcher and the client app all derive their pairs from here.
          </p>
        </div>
        <Button variant="outline" onClick={() => run(refresh, 'Refreshed')} disabled={busy} className="gap-2 shrink-0">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Refresh
        </Button>
      </div>

      {error && (
        <Card><CardContent className="pt-4 text-sm text-destructive">{error}</CardContent></Card>
      )}

      <div className="flex flex-wrap gap-4">
        <AssetColumn
          title="Base assets"
          hint="The traded coins (1st column) — e.g. BTC, ETH, SOL"
          items={bases}
          busy={busy}
          onAdd={a => run(() => addAsset('base', a), `Added base ${a}`)}
          onRemove={a => run(() => removeAsset('base', a), `Removed base ${a}`)}
          onToggle={(a, act) => run(() => setActive('base', a, act), `${act ? 'Enabled' : 'Disabled'} base ${a}`)}
        />
        <AssetColumn
          title="Quote assets / stablecoins"
          hint="The settlement coins (2nd column) — e.g. USDT, USDC"
          items={quotes}
          busy={busy}
          onAdd={a => run(() => addAsset('quote', a), `Added quote ${a}`)}
          onRemove={a => run(() => removeAsset('quote', a), `Removed quote ${a}`)}
          onToggle={(a, act) => run(() => setActive('quote', a, act), `${act ? 'Enabled' : 'Disabled'} quote ${a}`)}
        />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold flex items-center justify-between">
            <span>Active pairs preview</span>
            <Badge variant="secondary">{symbols.length}</Badge>
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Cross-product of active base × active quote. Only pairs actually listed on an exchange are tracked live.
          </p>
        </CardHeader>
        <Separator />
        <CardContent className="pt-4">
          {symbols.length === 0 ? (
            <span className="text-xs text-muted-foreground">No active pairs</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {symbols.map(s => (
                <code key={s} className="rounded bg-muted px-2 py-1 text-xs font-mono">{s}</code>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
