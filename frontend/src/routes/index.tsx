import { lazy, Suspense, type ReactNode } from 'react';
import { Navigate, type RouteObject } from 'react-router-dom';
import { Spin } from 'antd';
import { AppLayout } from '@/components/layout';

const DashboardPage = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.DashboardPage })));
const DiscoveryPage = lazy(() => import('@/pages/Discovery').then((m) => ({ default: m.DiscoveryPage })));
const FundsPage = lazy(() => import('@/pages/Funds').then((m) => ({ default: m.FundsPage })));
const FundDetailPage = lazy(() => import('@/pages/Funds/FundDetail').then((m) => ({ default: m.FundDetailPage })));
const StrategiesPage = lazy(() => import('@/pages/Strategies').then((m) => ({ default: m.StrategiesPage })));
const StrategyDetailPage = lazy(() => import('@/pages/Strategies/StrategyDetail').then((m) => ({ default: m.StrategyDetailPage })));
const BacktestsPage = lazy(() => import('@/pages/Backtests').then((m) => ({ default: m.BacktestsPage })));
const BacktestDetailPage = lazy(() => import('@/pages/Backtests/BacktestDetail').then((m) => ({ default: m.BacktestDetailPage })));
const ComparePage = lazy(() => import('@/pages/Backtests/Compare').then((m) => ({ default: m.ComparePage })));
const SimulationsPage = lazy(() => import('@/pages/Simulations').then((m) => ({ default: m.SimulationsPage })));
const SimulationDetailPage = lazy(() => import('@/pages/Simulations/SimulationDetail').then((m) => ({ default: m.SimulationDetailPage })));
const AdvisorPage = lazy(() => import('@/pages/Advisor').then((m) => ({ default: m.AdvisorPage })));
const AIPage = lazy(() => import('@/pages/AI').then((m) => ({ default: m.AIPage })));
const SettingsPage = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.SettingsPage })));
const NotFoundPage = lazy(() => import('@/pages/NotFound').then((m) => ({ default: m.NotFoundPage })));

const pageFallback = (
  <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
    <Spin />
  </div>
);

const withSuspense = (node: ReactNode) => (
  <Suspense fallback={pageFallback}>{node}</Suspense>
);

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: withSuspense(<DashboardPage />) },
      { path: 'discovery', element: withSuspense(<DiscoveryPage />) },
      { path: 'funds', element: withSuspense(<FundsPage />) },
      { path: 'funds/:code', element: withSuspense(<FundDetailPage />) },
      { path: 'strategies', element: withSuspense(<StrategiesPage />) },
      { path: 'strategies/:id', element: withSuspense(<StrategyDetailPage />) },
      { path: 'backtests', element: withSuspense(<BacktestsPage />) },
      { path: 'backtests/compare', element: withSuspense(<ComparePage />) },
      { path: 'backtests/:runId', element: withSuspense(<BacktestDetailPage />) },
      { path: 'simulations', element: withSuspense(<SimulationsPage />) },
      { path: 'simulations/:id', element: withSuspense(<SimulationDetailPage />) },
      { path: 'advisor', element: withSuspense(<AdvisorPage />) },
      { path: 'ai', element: withSuspense(<AIPage />) },
      { path: 'settings', element: withSuspense(<SettingsPage />) },
      { path: '404', element: withSuspense(<NotFoundPage />) },
      { path: '*', element: <Navigate to="/404" replace /> },
    ],
  },
];
