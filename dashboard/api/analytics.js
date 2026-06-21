// Vercel Node serverless function — per-video YouTube Analytics summary
// (views, watch time, avg view duration, impressions + CTR when available).
// Uses a refresh token (re-authed with yt-analytics.readonly scope) stored as
// env vars — never exposed to the browser.
const CLIENT_ID = process.env.YOUTUBE_CLIENT_ID;
const CLIENT_SECRET = process.env.YOUTUBE_CLIENT_SECRET;
const REFRESH_TOKEN = process.env.YOUTUBE_REFRESH_TOKEN;

async function getAccessToken() {
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      refresh_token: REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(`Token refresh failed: ${JSON.stringify(data)}`);
  return data.access_token;
}

async function queryAnalytics(accessToken, videoId, metrics) {
  const params = new URLSearchParams({
    ids: "channel==MINE",
    startDate: "2020-01-01",
    endDate: new Date().toISOString().slice(0, 10),
    metrics,
    dimensions: "video",
    filters: `video==${videoId}`,
  });
  const r = await fetch(`https://youtubeanalytics.googleapis.com/v2/reports?${params}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const data = await r.json();
  if (!r.ok) throw new Error(JSON.stringify(data));
  return data;
}

export default async function handler(req, res) {
  const videoId = req.query.videoId;
  if (!videoId) return res.status(400).json({ error: "videoId is required" });

  try {
    const accessToken = await getAccessToken();

    let row;
    try {
      const full = await queryAnalytics(accessToken, videoId, "views,estimatedMinutesWatched,averageViewDuration,impressions,impressionsClickThroughRate");
      row = full.rows?.[0];
    } catch {
      // Impressions/CTR metrics need a data threshold — fall back without them.
      const basic = await queryAnalytics(accessToken, videoId, "views,estimatedMinutesWatched,averageViewDuration");
      row = basic.rows?.[0];
    }

    if (!row) return res.status(200).json({ videoId, available: false });

    const [views, minutesWatched, avgViewDurationSec, impressions, ctr] = [row[1], row[2], row[3], row[4], row[5]];
    return res.status(200).json({
      videoId,
      available: true,
      views: views ?? null,
      watchTimeHours: minutesWatched != null ? Math.round((minutesWatched / 60) * 10) / 10 : null,
      avgViewDurationMin: avgViewDurationSec != null ? Math.round((avgViewDurationSec / 60) * 10) / 10 : null,
      impressions: impressions ?? null,
      impressionsCtrPct: ctr != null ? Math.round(ctr * 100) / 100 : null,
    });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
}
