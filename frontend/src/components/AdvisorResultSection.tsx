import type { ReactNode } from 'react';
import { Card } from 'antd';

export function AdvisorResultSection({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card title={title} size="small" extra={extra} style={{ marginBottom: 16 }}>
      {children}
    </Card>
  );
}
