import type { ReactNode } from 'react';

export function DetailSection({
  title,
  description,
  extra,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`detail-section${className ? ` ${className}` : ''}`}>
      <div className="detail-section-title">
        <div>
          <h3>{title}</h3>
          {description && <p>{description}</p>}
        </div>
        {extra}
      </div>
      {children}
    </div>
  );
}
