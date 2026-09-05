import * as React from 'react';

export function getStrictContext<T>(name: string) {
  const Context = React.createContext<T | undefined>(undefined);
  Context.displayName = name;

  function useContext() {
    const context = React.useContext(Context);
    if (context === undefined) {
      throw new Error(`useContext must be used within a ${name}Provider`);
    }
    return context;
  }

  return [Context.Provider, useContext] as const;
}
