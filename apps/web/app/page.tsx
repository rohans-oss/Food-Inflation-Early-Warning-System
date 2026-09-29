"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ROLE_HOME } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function Home() {
  const { user, ready } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (ready) router.replace(user ? ROLE_HOME[user.role] : "/login");
  }, [ready, user, router]);
  return <div className="p-8 text-sm text-muted">Loading…</div>;
}
