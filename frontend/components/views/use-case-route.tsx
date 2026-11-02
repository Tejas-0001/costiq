"use client";
import { useParams } from "next/navigation";
import { UseCasePage } from "./catalog";

export function UseCaseFromRoute() {
  const { id } = useParams<{ id: string }>();
  return <UseCasePage id={id} />;
}
