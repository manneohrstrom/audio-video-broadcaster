"""How long the rig can keep recording, from the free space where each camera's
footage lands. The hub page shows it; hubd stops the take when it runs out.

Two kinds of limit (all sizes in bytes, rates in bytes per second, times in seconds
since the take started; before REC, elapsed is 0):

- Each Pi camera's own SD card: the take is written there, and nothing is deleted
  on a camera while it records, so free space (less a small reserve) is the limit.
- The hub's SD card. It holds the phones' uploads during the take, and afterwards
  ingest pulls every Pi camera of the take and makes two working copies (the
  rewrap's .mkv and the .mov) of one camera at a time. For a take of length T:

      phones_total*(T - elapsed) + pis_total*T + 2*largest*T  <=  free_now - reserve

  where free_now already excludes what the phones uploaded so far. Footage of earlier
  takes still waiting for ingest is either already on the hub (phones: in free_now)
  or is ingested before this take (Pi cameras), so it doesn't add to the peak.

The STUDIO drive is not a limit: if it fills up, ingest waits and nothing is lost."""

MBPS = 1e6 / 8          # bytes per second in 1 Mbps


def limits(hub_free_b, hub_reserve_b, pis, phones, elapsed_s=0.0, node_reserve_b=0):
    """pis: {name: (free_bytes, rate_Bps)}; phones: {name: rate_Bps}.
    Returns {"total_s", "remaining_s", "limited_by", "per": {limit: total_s}}.
    total_s is None if nothing records (no cameras)."""
    per = {}
    for n, (free, rate) in pis.items():
        if free is not None and rate > 0:
            per[n] = elapsed_s + max(0.0, free - node_reserve_b) / rate
    rates = [r for _, r in pis.values()] + list(phones.values())
    if hub_free_b is not None and rates:
        ph = sum(phones.values())
        need = ph + sum(r for _, r in pis.values()) + 2 * max(rates)
        per["hub"] = max(0.0, hub_free_b - hub_reserve_b + ph * elapsed_s) / need
    if not per:
        return {"total_s": None, "remaining_s": None, "limited_by": None, "per": {}}
    by = min(per, key=per.get)
    return {"total_s": round(per[by]), "remaining_s": round(max(0.0, per[by] - elapsed_s)),
            "limited_by": by, "per": {k: round(v) for k, v in per.items()}}


def drive_fit(free_b, waiting_b, rate_Bps, elapsed_s=0.0, reserve_b=0):
    """Information only (never stops a take): how long a take fits on the STUDIO drive
    once ingest has copied it, after the footage still waiting for ingest.
    free_b None (drive not plugged in, or never read) or no rate -> None."""
    if free_b is None or rate_Bps <= 0:
        return None
    total = max(0.0, free_b - waiting_b - reserve_b) / rate_Bps
    return {"total_s": round(total), "remaining_s": round(max(0.0, total - elapsed_s)),
            "free_gb": round(free_b / 1e9, 1), "waiting_gb": round(waiting_b / 1e9, 1)}


def measured_rate(written_b, elapsed_s, default_Bps, min_s=60.0):
    """Rate from what a camera has written so far, once there is enough to go on.
    Never below half the default (protects against an early, still-dark scene)."""
    if written_b is None or elapsed_s < min_s or written_b <= 0:
        return default_Bps
    return max(written_b / elapsed_s, default_Bps / 2)
