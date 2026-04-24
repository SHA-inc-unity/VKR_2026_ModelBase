'use client';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  type TooltipProps,
} from 'recharts';

// ── Types ─────────────────────────────────────────────────────────────────────
export interface StepPoint {
  step: number;
  loss?: number;
  val_loss?: number;
}

export interface ProgressLineProps {
  points: StepPoint[];
  /** Chart height in px. @default 180 */
  height?: number;
}

// ── Constants ─────────────────────────────────────────────────────────────────
const MUTED_FG = 'hsl(215 20% 65%)';
const PRIMARY  = 'hsl(217 91% 60%)';
const WARNING  = 'hsl(38 92% 50%)';
const BG_CARD  = 'hsl(222 47% 16%)';
const BORDER   = 'hsl(217 33% 22%)';

// ── Custom tooltip ────────────────────────────────────────────────────────────
function CustomTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background:   BG_CARD,
        border:       `1px solid ${BORDER}`,
        borderRadius: 6,
        padding:      '6px 10px',
        fontSize:     12,
      }}
    >
      {payload.map(p => (
        <p
          key={String(p.dataKey)}
          style={{ color: p.stroke as string, margin: '2px 0' }}
        >
          {p.name}: {Number(p.value).toFixed(2)}
        </p>
      ))}
    </div>
  );
}

// ── Custom dot — rendered only at the last data point ────────────────────────
interface DotProps {
  cx?: number;
  cy?: number;
  index?: number;
}

function makeLastDot(dataLength: number, fillColor: string) {
  return function LastDot({ cx, cy, index }: DotProps) {
    if (index !== dataLength - 1 || cx === undefined || cy === undefined) {
      return <g />;
    }
    return (
      <circle
        cx={cx}
        cy={cy}
        r={4}
        fill={fillColor}
        stroke={BG_CARD}
        strokeWidth={2}
      />
    );
  };
}

// ── Component ─────────────────────────────────────────────────────────────────
export function ProgressLine({ points, height = 180 }: ProgressLineProps) {
  const hasValLoss = points.some(p => p.val_loss !== undefined);

  const LossDot    = makeLastDot(points.length, PRIMARY);
  const ValLossDot = makeLastDot(points.length, WARNING);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
        <XAxis
          dataKey="step"
          tick={{ fill: MUTED_FG, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: MUTED_FG, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={36}
        />
        <Tooltip content={<CustomTooltip />} />
        {hasValLoss && (
          <Legend wrapperStyle={{ fontSize: 11, color: MUTED_FG }} />
        )}
        <Line
          type="monotone"
          dataKey="loss"
          stroke={PRIMARY}
          strokeWidth={2}
          dot={<LossDot />}
          activeDot={{ r: 4, fill: PRIMARY }}
          name="loss"
          isAnimationActive={false}
        />
        {hasValLoss && (
          <Line
            type="monotone"
            dataKey="val_loss"
            stroke={WARNING}
            strokeWidth={2}
            dot={<ValLossDot />}
            activeDot={{ r: 4, fill: WARNING }}
            name="val_loss"
            isAnimationActive={false}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
