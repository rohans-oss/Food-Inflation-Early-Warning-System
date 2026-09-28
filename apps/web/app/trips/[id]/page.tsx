"use client";

import { useParams } from "next/navigation";
import Shell from "@/components/Shell";
import TripLive from "@/components/TripLive";

export default function TripPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <Shell roles={["farmer", "fpo", "driver", "fleet_owner", "trader", "lender"]} title={`Trip #${id}`}>
      {() => <TripLive tripId={Number(id)} />}
    </Shell>
  );
}
