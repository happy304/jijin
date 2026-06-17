import { useCallback } from 'react';
import {
  appendEmptyAdvisorPosition,
  removeAdvisorPositionAt,
  updateAdvisorPositionAt,
  type AdvisorPositionItem,
} from '@/utils/advisorPositions';

export function useAdvisorPositionEditorActions({
  setPositions,
}: {
  setPositions: React.Dispatch<React.SetStateAction<AdvisorPositionItem[]>>;
}) {
  const addPosition = useCallback(() => {
    setPositions((prev) => appendEmptyAdvisorPosition(prev));
  }, [setPositions]);

  const removePosition = useCallback((index: number) => {
    setPositions((prev) => removeAdvisorPositionAt(prev, index));
  }, [setPositions]);

  const updatePosition = useCallback((index: number, field: keyof AdvisorPositionItem, value: string | number) => {
    setPositions((prev) => updateAdvisorPositionAt(prev, index, field, value));
  }, [setPositions]);

  return {
    addPosition,
    removePosition,
    updatePosition,
  };
}
