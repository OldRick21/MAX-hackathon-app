import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { isAbort } from '../api/http';

export type AsyncState<T> =
  | { status: 'loading'; data?: T; error?: undefined }
  | { status: 'ready'; data: T; error?: undefined }
  | { status: 'error'; data?: T; error: unknown };

/**
 * Загрузка данных с отменой устаревших запросов.
 * reload() перезапрашивает; при повторной загрузке прежние данные остаются в data.
 */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: DependencyList) {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const ctrl = new AbortController();
    setState(s => ({ status: 'loading', data: s.data }));
    fnRef.current(ctrl.signal).then(
      data => { if (!ctrl.signal.aborted) setState({ status: 'ready', data }); },
      error => { if (!ctrl.signal.aborted && !isAbort(error)) setState(s => ({ status: 'error', error, data: s.data })); },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick(t => t + 1), []);
  const mutate = useCallback((update: (prev: T | undefined) => T) => {
    setState(s => ({ status: 'ready', data: update(s.data) }));
  }, []);
  return { ...state, reload, mutate };
}
