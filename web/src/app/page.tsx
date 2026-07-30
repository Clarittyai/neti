import { redirect } from "next/navigation";

// The gate is the product. Everything else is context, so it is not the front door.
export default function Page() {
  redirect("/gate");
}
