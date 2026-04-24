'use client';
import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';

interface CollapsibleProps {
  title: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
  /** Optional controlled-open override. When provided, component is controlled. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/**
 * Minimal collapsible section — header acts as toggle, chevron rotates, body
 * is mounted only when open to avoid paying lazy-fetch cost until expanded.
 */
export function Collapsible({
  title,
  defaultOpen = false,
  children,
  className,
  open,
  onOpenChange,
}: CollapsibleProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : internalOpen;

  const toggle = () => {
    const next = !isOpen;
    if (!isControlled) setInternalOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div className={cn('rounded-lg border border-border bg-card', className)}>
      <button
        type="button"
        onClick={toggle}
        className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-accent/40 transition-colors rounded-t-lg"
      >
        <span className="text-sm font-semibold">{title}</span>
        <ChevronDown
          className={cn(
            'w-4 h-4 text-muted-foreground transition-transform duration-200',
            isOpen && 'rotate-180',
          )}
        />
      </button>
      {isOpen && <div className="border-t border-border">{children}</div>}
    </div>
  );
}
