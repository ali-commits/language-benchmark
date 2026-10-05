import { HttpError } from "./validation.js";
import { abortError } from "./cancellation.js";
// One active native hash, plus a finite queue of request-scoped tasks.
export class HashQueue {
  #active = false;
  #pending = [];
  constructor(maxPending = 64) {
    this.maxPending = maxPending;
  }
  run(work, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(abortError());
        return;
      }
      if (this.#active && this.#pending.length >= this.maxPending) {
        reject(new HttpError(503, "service unavailable"));
        return;
      }
      const task = { work, signal, resolve, reject, aborted: false };
      task.cancel = () => {
        task.aborted = true;
        const index = this.#pending.indexOf(task);
        if (index !== -1) {
          this.#pending.splice(index, 1);
          signal.removeEventListener("abort", task.cancel);
        }
        reject(abortError());
      };
      signal?.addEventListener("abort", task.cancel, { once: true });
      this.#pending.push(task);
      this.#drain();
    });
  }
  #drain() {
    if (this.#active) return;
    const task = this.#pending.shift();
    if (!task) return;
    this.#active = true;
    Promise.resolve()
      .then(() => {
        if (task.aborted || task.signal?.aborted) throw abortError();
        return task.work();
      })
      .then(task.resolve, task.reject)
      .finally(() => {
        task.signal?.removeEventListener("abort", task.cancel);
        this.#active = false;
        this.#drain();
      });
  }
}
