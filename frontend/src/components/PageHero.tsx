import type { ReactNode } from 'react';

export interface PageHeroProps {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  stats?: ReactNode;
  variant?: 'page' | 'detail' | 'section';
  className?: string;
}

export function PageHero({
  eyebrow,
  title,
  description,
  meta,
  actions,
  stats,
  variant = 'page',
  className,
}: PageHeroProps) {
  if (variant === 'detail') {
    return (
      <section className={`detail-hero${className ? ` ${className}` : ''}`}>
        <div className="detail-hero-grid">
          <div>
            {eyebrow && <div className="page-eyebrow">{eyebrow}</div>}
            <h2>{title}</h2>
            {meta && <div className="detail-meta">{meta}</div>}
            {description && <p style={{ marginTop: 12 }}>{description}</p>}
            {actions && <div className="detail-actions">{actions}</div>}
          </div>
          {stats && <div className="detail-stat-grid">{stats}</div>}
        </div>
      </section>
    );
  }

  const sectionClass = variant === 'section' ? 'section-hero' : 'page-hero';
  const contentClass = variant === 'section' ? 'section-hero-content' : 'page-hero-content';
  const actionsClass = variant === 'section' ? 'section-hero-actions' : 'page-hero-actions';
  const HeadingTag = variant === 'section' ? 'h2' : 'h1';

  return (
    <section className={`${sectionClass}${className ? ` ${className}` : ''}`}>
      <div className={contentClass}>
        {eyebrow && <div className="page-eyebrow">{eyebrow}</div>}
        <HeadingTag>{title}</HeadingTag>
        {meta}
        {description && <p>{description}</p>}
        {actions && <div className={actionsClass}>{actions}</div>}
      </div>
      {stats}
    </section>
  );
}
