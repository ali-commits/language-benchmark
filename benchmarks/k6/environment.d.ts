// k6 supplies console without the browser DOM. Only the used surface is declared.
declare const console: { log(...values: unknown[]): void };
