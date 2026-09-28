"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ROLE_HOME } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Home() {
  const { user, ready } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (ready) router.replace(user ? ROLE_HOME[user.role] : "/login");
  }, [ready, user, router]);
  return <div className="p-8 text-sm text-slate-500">Loading…</div>;
}
