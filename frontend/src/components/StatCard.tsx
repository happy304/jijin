import type { CSSProperties, ReactNode } from 'react';
import { Card } from 'antd';

export interface StatCardProps {
  label: ReactNode;
  value: ReactNode;
  note?: ReactNode;
  color?: string;
  className?: string;
  size?: 'default' | 'small';
  valueStyle?: CSSProperties;
}

export function StatCard({
  label,
  value,
  note,
  color,
  className,
  size = 'default',
  valueStyle,
}: StatCardProps) {
  const mergedValueStyle = color ? { color, ...valueStyle } : valueStyle;

  return (
    <Card className={`detail-stat-card${className ? ` ${className}` : ''}`} size={size === 'small' ? 'small' : undefined}>
      <div className="detail-stat-label">{label}</div>
      <div className="detail-stat-value" style={mergedValueStyle}>{value}</div>
      {note && <div className="detail-stat-note">{note}</div>}
    </Card>
  );
}
