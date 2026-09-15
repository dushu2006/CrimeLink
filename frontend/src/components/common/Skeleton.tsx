export function SkeletonLine({ width = "80%" }: { width?: string }) {
  return <div className="skeleton-line" style={{ width }} role="status" aria-label="Loading" />;
}
export function SkeletonCard() {
  return (
    <div className="skeleton-card" role="status" aria-label="Loading">
      <SkeletonLine width="60%" />
      <SkeletonLine width="40%" />
      <SkeletonLine width="80%" />
    </div>
  );
}
export function SkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="skeleton-grid">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
export default SkeletonCard;
