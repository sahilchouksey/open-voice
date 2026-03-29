export type StoreListener<S, A> = (state: S, action: A) => void
export type SelectorListener<T> = (nextValue: T, previousValue: T) => void

export interface SelectorSubscriptionOptions<T> {
  equalityFn?: (left: T, right: T) => boolean
  emitCurrent?: boolean
}

export interface Store<S, A> {
  getState(): S
  dispatch(action: A): void
  subscribe(listener: StoreListener<S, A>): () => void
  subscribeSelector<T>(
    selector: (state: S) => T,
    listener: SelectorListener<T>,
    options?: SelectorSubscriptionOptions<T>,
  ): () => void
}

export function createStore<S, A>(
  initialState: S,
  reducer: (state: S, action: A) => S,
): Store<S, A> {
  let state = initialState
  const listeners = new Set<StoreListener<S, A>>()

  const getState = (): S => state

  const dispatch = (action: A): void => {
    const nextState = reducer(state, action)
    if (Object.is(nextState, state)) {
      return
    }
    state = nextState
    const snapshot = [...listeners]
    for (const listener of snapshot) {
      listener(state, action)
    }
  }

  const subscribe = (listener: StoreListener<S, A>): (() => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  const subscribeSelector = <T>(
    selector: (nextState: S) => T,
    listener: SelectorListener<T>,
    options: SelectorSubscriptionOptions<T> = {},
  ): (() => void) => {
    const equalityFn = options.equalityFn ?? Object.is
    let previousValue = selector(state)

    if (options.emitCurrent) {
      listener(previousValue, previousValue)
    }

    return subscribe((nextState) => {
      const nextValue = selector(nextState)
      if (equalityFn(nextValue, previousValue)) {
        return
      }
      const oldValue = previousValue
      previousValue = nextValue
      listener(nextValue, oldValue)
    })
  }

  return {
    getState,
    dispatch,
    subscribe,
    subscribeSelector,
  }
}
