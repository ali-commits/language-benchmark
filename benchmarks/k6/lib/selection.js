export function choice(iteration, seed) {
  let x = (iteration + seed) | 0;
  x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
  x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
  return (x ^ (x >>> 16)) >>> 0;
}
export function operation(n) {
  const p = n % 100;
  if (p < 60) return 'feed';
  if (p < 75) return 'comments';
  if (p < 85) return 'like';
  if (p < 90) return 'post';
  if (p < 95) return 'comment';
  return 'login';
}
