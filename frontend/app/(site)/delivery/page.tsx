import { redirect } from "next/navigation";

// Delivery and migration now live inside the deployment planner's questions and blueprint.
export default function Page() {
  redirect("/deploy");
}
