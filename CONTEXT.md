# Suno Integration Context

This context names the domain concepts used by the Home Assistant Suno integration. It exists so architecture discussions can use stable domain language when shaping modules and seams.

## Language

**Suno Library**:
The user's fetched Suno songs, liked songs, playlists, playlist clips, credits, and Suno identity as represented inside Home Assistant.
_Avoid_: feed data, coordinator data, API data

**Partial Suno Library**:
A **Suno Library** snapshot where one or more sections came from the last known data because the latest **Library Refresh** could not fetch them.
_Avoid_: empty fallback, degraded data

**Library Refresh**:
A cycle that fetches Suno data and reconciles it into a current **Suno Library** snapshot.
_Avoid_: update tick, API fetch, background task

**Clip Lineage**:
The parent and root-ancestor relationship between derived, edited, or remixed Suno clips.
_Avoid_: ancestor helper, remix lookup

**External Lineage Root**:
A **Clip Lineage** root identified by id but not present as a clip in the current **Suno Library**.
_Avoid_: missing parent, orphan root

**Unavailable Lineage**:
A **Clip Lineage** result where Suno lookup succeeds but the chain is hidden, deleted, private, or otherwise not revealable.
_Avoid_: failed lookup, unresolved remix

**Album Details**:
The album grouping and lineage metadata used when presenting, downloading, or tagging Suno clips.
_Avoid_: tag data, root title

**Stored Library**:
A persisted **Suno Library** snapshot used to restore Home Assistant state before or instead of a successful **Library Refresh**.
_Avoid_: cache, store payload

**Suno Identity**:
The display name from Suno clip data that represents the library owner inside Home Assistant.
_Avoid_: Clerk username, login handle

## Relationships

- A **Library Refresh** produces one **Suno Library** snapshot.
- A **Library Refresh** may produce a **Partial Suno Library** when Suno is partly unavailable.
- A **Suno Library** may include many clips with **Clip Lineage**.
- **Clip Lineage** may resolve to an **External Lineage Root**.
- **Clip Lineage** may resolve to **Unavailable Lineage**.
- **Clip Lineage** determines **Album Details** for remixed clips.
- An **External Lineage Root** uses `Remixes of <short-root-id>` for **Album Details**.
- **Unavailable Lineage** uses `Remixes of unknown root` for **Album Details**.
- A **Stored Library** restores a previous **Suno Library** snapshot.
- A **Suno Identity** belongs to one **Suno Library**.

## Example dialogue

> **Dev:** "Should the **Library Refresh** update the Home Assistant entry title directly?"
> **Domain expert:** "No, it should report the new **Suno Identity**; the coordinator applies the Home Assistant title change."
> **Dev:** "Can we show a remix before its **Clip Lineage** is resolved?"
> **Domain expert:** "Only if its **Album Details** are not used yet; published remix clips must have correct album grouping and lineage metadata."

## Flagged ambiguities

- "cache" can mean the audio cache or the **Stored Library**. Use **Stored Library** for persisted library snapshots and "audio cache" for cached media files.
- "display name" can mean the Clerk login handle or **Suno Identity**. Use **Suno Identity** for the Suno owner name shown in Home Assistant.
