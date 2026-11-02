"use server";
// Demo sign-in: the chosen persona is kept in an http-only cookie and added to API calls by the /api route handler.
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const PERSONA = /^[a-z_]{2,40}$/;

export async function choosePersona(persona: string, next: string = "/command") {
  if (!PERSONA.test(persona)) throw new Error("Invalid persona");
  (await cookies()).set("costiq_persona", persona, { httpOnly: true, sameSite: "lax", path: "/", maxAge: 60 * 60 * 12 });
  redirect(next.startsWith("/") && !next.startsWith("//") && next !== "/" ? next : "/command");
}

export async function signOut() {
  (await cookies()).delete("costiq_persona");
  redirect("/sign-in");
}
