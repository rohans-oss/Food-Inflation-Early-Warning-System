"use client";

import { useMemo, useState } from "react";

import { api } from "@/lib/api";
import { MAP_SOURCE, USING_PUBLIC_OSM } from "@/lib/mapstyle";
import { num } from "@/lib/format";

import { MapMarker, MapView } from "./MapView";
import { Badge, Button, Card, ErrorNote, Field, inputCls, Note, Table, Td, useAction, useApi } from "./ui";

/** V3-3 (backlog 3): confirm each mandi's yard location on a map. OpenStreetMap suggestions are only suggestions;
 * a person confirms. The geofence (reached_mandi), routes, the optimizer and graph distance edges use this point. */
export function MandiLocations() {
  const list = useApi<any[]>("/admin/mandis/locations");
  const [sel, setSel] = useState<any>(null);
  const [pt, setPt] = useState<{ lat: number; lon: number } | null>(null);
  const [radius, setRadius] = useState("500");
  const [cands, setCands] = useState<any>(null);
  const act = useAction();
  const pick = (m: any) => { setSel(m); setPt(m.lat != null ? { lat: m.lat, lon: m.lon } : null); setRadius(String(m.geofence_radius_m)); setCands(null); };
  const suggest = () => act.run(async () => setCands(await api(`/admin/mandis/${sel.id}/osm-candidates`)));
  const save = (verify: boolean) => act.run(async () => {
    await api(`/admin/mandis/${sel.id}`, { method: "PATCH", body: { lat: pt!.lat, lon: pt!.lon, geofence_radius_m: Number(radius), coords_verified: verify } });
    list.reload();
    setSel({ ...sel, lat: pt!.lat, lon: pt!.lon, coords_verified: verify, geofence_radius_m: Number(radius) });
  });
  const markers = useMemo<MapMarker[]>(() => {
    const out: MapMarker[] = [];
    if (sel?.lat != null) out.push({ id: "cur", lat: sel.lat, lon: sel.lon, kind: "status", color: "#898781", size: 14, label: "Saved point", popup: "Saved point" });
    for (const [i, c] of (cands?.candidates ?? []).entries()) out.push({ id: `c${i}`, lat: c.lat, lon: c.lon, kind: "status", color: "#2a78d6", size: 12, label: c.name, popup: `${c.name} (${c.type}) — suggestion` });
    if (pt) out.push({ id: "new", lat: pt.lat, lon: pt.lon, kind: "mandi", label: "New point", popup: "New point (click the map to move)" });
    return out;
  }, [sel, cands, pt]);
  const verified = (list.data ?? []).filter((m) => m.coords_verified).length;

  return (
    <Card title="Mandi locations" action={<span className="text-xs text-muted">{verified} / {list.data?.length ?? 0} verified</span>}>
      {USING_PUBLIC_OSM && <Note>Map tiles: {MAP_SOURCE}. Its usage policy does not allow production traffic; self-host tiles before a pilot (docs/map-tiles.md).</Note>}
      <p className="mb-3 text-sm text-ink2">An unverified point is a town centre, not the yard. Arrival alerts fire inside the geofence around this point, so a
        wrong point means &quot;reached mandi&quot; never fires. Pick a mandi, check the suggestions, click the map on the yard gate, then confirm.</p>
      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <div className="max-h-[28rem] overflow-y-auto">
          <Table head={["Mandi", "Status"]}>
            {list.data?.map((m) => (
              <tr key={m.id} className={sel?.id === m.id ? "bg-page" : ""}>
                <Td><button className="text-left underline" onClick={() => pick(m)}>{m.name}</button><div className="text-xs text-muted">{m.district}</div></Td>
                <Td>{m.coords_verified ? <Badge kind="good">verified</Badge> : <Badge kind="warn">unverified</Badge>}</Td>
              </tr>
            ))}
          </Table>
        </div>
        <div className="space-y-2">
          {!sel ? <p className="text-sm text-muted">Pick a mandi.</p> : (
            <>
              <div className="flex flex-wrap items-end gap-2">
                <b>{sel.name}</b>
                <Button variant="secondary" onClick={suggest} disabled={act.busy}>Suggest from OpenStreetMap</Button>
              </div>
              <MapView height="h-72" markers={markers} fitKey={`${sel.id}-${cands ? cands.candidates.length : 0}`} onClick={(lat, lon) => setPt({ lat, lon })} />
              {cands && (
                <div className="text-sm">
                  {cands.candidates.length === 0 ? <p className="text-muted">No suggestions found; click the map instead.</p> : (
                    <ul className="space-y-1">
                      {cands.candidates.map((c: any, i: number) => (
                        <li key={i} className="flex flex-wrap items-center gap-2">
                          <button className="underline" onClick={() => setPt({ lat: c.lat, lon: c.lon })}>Use</button>
                          <span>{c.name}</span><span className="text-xs text-muted">{c.category}/{c.type} · {c.km_from_current ?? "?"} km from saved point</span>
                          <a className="text-xs underline" href={c.osm_url} target="_blank" rel="noreferrer">OSM</a>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="text-xs text-muted">{cands.attribution}. {cands.note}</p>
                </div>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <Field label="Latitude, longitude"><span className="font-mono text-sm">{pt ? `${num(pt.lat, 5)}, ${num(pt.lon, 5)}` : "click the map"}</span></Field>
                <Field label="Geofence (m)"><div className="w-28"><input className={inputCls} inputMode="numeric" value={radius} onChange={(e) => setRadius(e.target.value)} /></div></Field>
                <Button onClick={() => save(true)} disabled={!pt || act.busy}>Confirm location</Button>
                <Button variant="secondary" onClick={() => save(false)} disabled={!pt || act.busy}>Save, not yet verified</Button>
              </div>
              <ErrorNote error={act.error} />
            </>
          )}
        </div>
      </div>
    </Card>
  );
}
