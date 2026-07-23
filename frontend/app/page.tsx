import { redirect } from "next/navigation";

// Home → portfolio. The shell guard bounces to /login when there's no token.
export default function Home() {
  redirect("/portfolio");
}
