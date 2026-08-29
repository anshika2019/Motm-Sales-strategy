interface LoadingBubbleProps {
  label: string;
}

export default function LoadingBubble({ label }: LoadingBubbleProps) {
  return <div className="loading-bubble">{label}</div>;
}
