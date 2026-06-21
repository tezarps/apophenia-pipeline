// Vercel Node serverless function — CRUD for the `topics` queue.
// GET is open (mirrors what's already public via the publishable key client-side).
// POST/PATCH/DELETE require header x-dashboard-pin matching env DASHBOARD_PIN —
// the dashboard page itself is fully public with no login, so writes need this
// lightweight gate to stop a leaked link from letting randoms edit the queue.
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY;
const DASHBOARD_PIN = process.env.DASHBOARD_PIN;

function sbHeaders() {
  return {
    apikey: SUPABASE_SERVICE_KEY,
    Authorization: `Bearer ${SUPABASE_SERVICE_KEY}`,
    "Content-Type": "application/json",
  };
}

function checkPin(req) {
  return DASHBOARD_PIN && req.headers["x-dashboard-pin"] === DASHBOARD_PIN;
}

export default async function handler(req, res) {
  try {
    if (req.method === "GET") {
      const r = await fetch(`${SUPABASE_URL}/rest/v1/topics?order=id.desc`, { headers: sbHeaders() });
      const data = await r.json();
      return res.status(r.status).json(data);
    }

    if (!checkPin(req)) {
      return res.status(401).json({ error: "Invalid or missing PIN" });
    }

    if (req.method === "POST") {
      const { category, topic, angle } = req.body;
      if (!category || !topic) return res.status(400).json({ error: "category and topic are required" });
      const idRes = await fetch(`${SUPABASE_URL}/rest/v1/topics?select=id&order=id.desc&limit=1`, { headers: sbHeaders() });
      const idData = await idRes.json();
      const newId = (idData[0]?.id || 0) + 1;
      const r = await fetch(`${SUPABASE_URL}/rest/v1/topics`, {
        method: "POST",
        headers: { ...sbHeaders(), Prefer: "return=representation" },
        body: JSON.stringify([{ id: newId, category, topic, angle: angle || "", status: "pending", video_id: "", notes: "" }]),
      });
      const data = await r.json();
      return res.status(r.status).json(data);
    }

    if (req.method === "PATCH") {
      const { id, ...fields } = req.body;
      if (!id) return res.status(400).json({ error: "id is required" });
      const r = await fetch(`${SUPABASE_URL}/rest/v1/topics?id=eq.${id}`, {
        method: "PATCH",
        headers: { ...sbHeaders(), Prefer: "return=representation" },
        body: JSON.stringify(fields),
      });
      const data = await r.json();
      return res.status(r.status).json(data);
    }

    if (req.method === "DELETE") {
      const { id } = req.body;
      if (!id) return res.status(400).json({ error: "id is required" });
      const r = await fetch(`${SUPABASE_URL}/rest/v1/topics?id=eq.${id}`, {
        method: "DELETE",
        headers: sbHeaders(),
      });
      return res.status(r.status).json({ ok: r.ok });
    }

    return res.status(405).json({ error: "Method not allowed" });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
}
