import { useEffect, useRef } from 'react';
import type { UseMutationResult } from '@tanstack/react-query';
import type { AdvisorHoldingImportPosition, AdvisorPositionsResponse } from '@/api/advisor';
import {
  normalizeAdvisorPositionItem,
  toPersistedAdvisorPositionPayload,
  type AdvisorPositionItem,
} from '@/utils/advisorPositions';

export function useAdvisorPositionsSync({
  positions,
  persistedPositions,
  persistedPositionsFetched,
  replacePositionsMutation,
  setPositions,
}: {
  positions: AdvisorPositionItem[];
  persistedPositions?: AdvisorPositionsResponse;
  persistedPositionsFetched: boolean;
  replacePositionsMutation: UseMutationResult<AdvisorPositionsResponse, Error, { positions: AdvisorHoldingImportPosition[] }, unknown>;
  setPositions: (positions: AdvisorPositionItem[]) => void;
}) {
  const positionsHydratedRef = useRef(false);
  const skipNextPositionSyncRef = useRef(false);

  useEffect(() => {
    if (!persistedPositionsFetched || positionsHydratedRef.current) return;
    const serverPositions = (persistedPositions?.positions || []).map((item) => normalizeAdvisorPositionItem(item));
    if (serverPositions.length > 0) {
      skipNextPositionSyncRef.current = true;
      setPositions(serverPositions);
    } else if (persistedPositions?.status === 'success' && positions.length > 0) {
      replacePositionsMutation.mutate({ positions: toPersistedAdvisorPositionPayload(positions) });
    }
    positionsHydratedRef.current = true;
  }, [persistedPositionsFetched, persistedPositions, positions, replacePositionsMutation, setPositions]);

  useEffect(() => {
    if (!positionsHydratedRef.current) return;
    if (skipNextPositionSyncRef.current) {
      skipNextPositionSyncRef.current = false;
      return;
    }
    const timer = window.setTimeout(() => {
      replacePositionsMutation.mutate({ positions: toPersistedAdvisorPositionPayload(positions) });
    }, 400);
    return () => window.clearTimeout(timer);
  }, [positions, replacePositionsMutation]);

  return {
    skipNextPositionSyncRef,
  };
}
