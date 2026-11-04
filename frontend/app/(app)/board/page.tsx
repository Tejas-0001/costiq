import { BoardView } from "@/components/views/board";

export default function BoardPage() {
  return <BoardView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
