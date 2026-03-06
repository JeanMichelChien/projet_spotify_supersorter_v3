from __future__ import annotations

from datetime import datetime
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from src.auth import (
    AuthConfigurationError,
    AuthFlowError,
    AuthSession,
    authenticate_with_local_callback,
    create_auth_manager,
    get_cached_session,
    get_missing_scopes,
)
from src.cache_store import CacheStore
from src.automation_engine import sync_automation_rule
from src.automation_store import AutomationStore
from src.enrichment import (
    AUDIO_FEATURE_KEYS,
    collect_artist_ids,
    enrich_tracks_with_artist_genres,
    enrich_tracks_with_audio_features,
    normalize_playlist_tracks,
)
from src.exporters import filtered_tracks_to_csv_bytes, filtered_tracks_to_dataframe
from src.filter_engine import FilterConfig, apply_filters
from src.spotify_api import (
    SpotifyAPIError,
    fetch_all_playlists,
    fetch_artists_by_ids,
    fetch_audio_features_by_track_ids,
    fetch_playlist_tracks,
    fetch_saved_tracks,
    fetch_saved_tracks_total,
    get_current_user_profile,
)
from src.ui_helpers import build_genre_count_rows, build_playlist_select_options
from src.ui_helpers import build_genre_timeline_rows, summarize_genre_evolution

LIKED_SONGS_SOURCE_ID = "__liked_songs__"
LIKED_SONGS_SOURCE_NAME = "Liked Songs"

AUDIO_FEATURE_LABELS = {
    "danceability": "Danceability",
    "energy": "Energy",
    "valence": "Valence",
    "tempo": "Tempo",
    "acousticness": "Acousticness",
    "instrumentalness": "Instrumentalness",
    "speechiness": "Speechiness",
    "liveness": "Liveness",
}

AUDIO_DEFAULT_BOUNDS = {
    "danceability": (0.0, 1.0),
    "energy": (0.0, 1.0),
    "valence": (0.0, 1.0),
    "tempo": (0.0, 250.0),
    "acousticness": (0.0, 1.0),
    "instrumentalness": (0.0, 1.0),
    "speechiness": (0.0, 1.0),
    "liveness": (0.0, 1.0),
}


st.set_page_config(page_title="Spotify Playlist SuperSorter", layout="wide")
st.title("Spotify Playlist SuperSorter")
st.caption("Filter your playlists with genre-aware relevance scoring and explainable matches.")

cache_store = CacheStore("cache")
automation_store = AutomationStore("cache")


@st.cache_data(show_spinner=False)
def _current_year() -> int:
    return datetime.now().year


def _compute_year_bounds(tracks: list[dict[str, Any]]) -> tuple[int, int]:
    years = [track.get("release_year") for track in tracks if isinstance(track.get("release_year"), int)]
    if not years:
        return (1950, _current_year())
    lower = min(years)
    upper = max(years)
    if lower == upper:
        lower = max(1900, lower - 1)
        upper = min(_current_year(), upper + 1)
    return (lower, upper)


def _compute_audio_bounds(tracks: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    bounds = dict(AUDIO_DEFAULT_BOUNDS)
    tempos = [float(track["tempo"]) for track in tracks if track.get("tempo") is not None]
    if tempos:
        lower = max(0.0, min(tempos))
        upper = max(lower, max(tempos))
        if lower == upper:
            upper = lower + 1.0
        bounds["tempo"] = (round(lower, 1), round(upper, 1))
    return bounds


def _extract_selected_row_index(table_state: Any) -> int | None:
    if table_state is None:
        return None

    selection = None
    if hasattr(table_state, "selection"):
        selection = getattr(table_state, "selection")
    elif isinstance(table_state, dict):
        selection = table_state.get("selection")

    if selection is None:
        return None

    rows = None
    if hasattr(selection, "rows"):
        rows = getattr(selection, "rows")
    elif isinstance(selection, dict):
        rows = selection.get("rows")

    if not rows:
        return None

    try:
        return int(rows[0])
    except (TypeError, ValueError, IndexError):
        return None


def _get_playlist_data(
    session: AuthSession,
    playlist_id: str,
    force_refresh: bool,
) -> tuple[list[dict[str, Any]], bool, str | None, str | None, str]:
    if not force_refresh:
        cached = cache_store.get_playlist_cache(playlist_id)
        if cached and isinstance(cached.get("tracks"), list):
            return (
                cached.get("tracks", []),
                bool(cached.get("audio_features_available", False)),
                cached.get("audio_warning"),
                cached.get("fetched_at"),
                "cache",
            )

    source = "spotify"
    if playlist_id == LIKED_SONGS_SOURCE_ID:
        playlist_items = fetch_saved_tracks(session.client)
        source = "spotify-liked-songs"
    else:
        playlist_items = fetch_playlist_tracks(session.client, playlist_id)
    tracks = normalize_playlist_tracks(playlist_id, playlist_items)

    artist_ids = collect_artist_ids(tracks)
    genres_by_artist_id: dict[str, list[str]] = {}
    missing_artist_ids: list[str] = []

    for artist_id in artist_ids:
        cached_artist = cache_store.get_artist_cache(artist_id)
        if cached_artist and isinstance(cached_artist.get("genres"), list):
            genres_by_artist_id[artist_id] = cached_artist["genres"]
        else:
            missing_artist_ids.append(artist_id)

    if missing_artist_ids:
        fetched_artists = fetch_artists_by_ids(session.client, missing_artist_ids)
        for artist in fetched_artists:
            artist_id = artist.get("id")
            if not artist_id:
                continue
            genres = artist.get("genres", [])
            genres_by_artist_id[artist_id] = genres
            cache_store.set_artist_cache(artist_id, genres)

    tracks = enrich_tracks_with_artist_genres(tracks, genres_by_artist_id)

    track_ids = [track.get("track_id") for track in tracks if track.get("track_id")]
    audio_features: dict[str, dict[str, Any]] = {}
    missing_audio_ids: list[str] = []

    for track_id in track_ids:
        cached_audio = cache_store.get_audio_cache(track_id)
        if cached_audio and isinstance(cached_audio.get("features"), dict):
            audio_features[track_id] = cached_audio["features"]
        else:
            missing_audio_ids.append(track_id)

    audio_features_available = True
    audio_warning: str | None = None

    if missing_audio_ids:
        fetched_audio, endpoint_available, warning = fetch_audio_features_by_track_ids(
            session.client, missing_audio_ids
        )
        if endpoint_available:
            for track_id, feature_row in fetched_audio.items():
                audio_features[track_id] = feature_row
                cache_store.set_audio_cache(track_id, feature_row)
        else:
            audio_features_available = False
            audio_warning = warning

    has_cached_or_fetched_audio = any(
        isinstance(row, dict) and any(row.get(key) is not None for key in AUDIO_FEATURE_KEYS)
        for row in audio_features.values()
    )
    if not audio_features_available and has_cached_or_fetched_audio:
        audio_features_available = True
        audio_warning = (
            (audio_warning + " ") if audio_warning else ""
        ) + "Using cached audio features where available."

    tracks = enrich_tracks_with_audio_features(tracks, audio_features, audio_features_available)
    global_audio_available = any(track.get("audio_features_available") for track in tracks)

    fetched_at = datetime.utcnow().replace(microsecond=0).isoformat() + "+00:00"
    cache_store.set_playlist_cache(
        playlist_id,
        {
            "playlist_id": playlist_id,
            "fetched_at": fetched_at,
            "audio_features_available": global_audio_available,
            "audio_warning": audio_warning,
            "tracks": tracks,
        },
    )

    return tracks, global_audio_available, audio_warning, fetched_at, source


def _get_active_session(auth_manager) -> AuthSession | None:
    session = st.session_state.get("auth_session")
    if session:
        return session

    cached = get_cached_session(auth_manager)
    if cached:
        st.session_state["auth_session"] = cached
        return cached
    return None


def _render_auth(auth_manager) -> AuthSession | None:
    st.sidebar.header("Login")

    session = _get_active_session(auth_manager)

    if not session:
        st.sidebar.info("Authenticate to load playlists and metadata.")
        if st.sidebar.button("Login with Spotify"):
            with st.spinner("Waiting for Spotify authorization in your browser..."):
                try:
                    new_session = authenticate_with_local_callback(auth_manager)
                    st.session_state["auth_session"] = new_session
                    st.rerun()
                except AuthFlowError as exc:
                    st.sidebar.error(str(exc))
        return None

    profile_name = "Spotify user"
    try:
        profile = get_current_user_profile(session.client)
        profile_name = profile.get("display_name") or profile.get("id") or profile_name
    except Exception:  # noqa: BLE001
        pass

    st.sidebar.success(f"Connected as {profile_name}")

    if st.sidebar.button("Reconnect"):
        st.session_state.pop("auth_session", None)
        st.rerun()

    missing_scopes = get_missing_scopes(session.token_info)
    if missing_scopes:
        st.sidebar.warning(
            "Token missing required scope(s): "
            + ", ".join(missing_scopes)
            + ". Use Reconnect to re-authorize."
        )

    return session


def _render_filter_controls(
    tracks: list[dict[str, Any]],
    audio_available: bool,
) -> FilterConfig:
    st.sidebar.header("Filters")

    mode = st.sidebar.radio("Combine groups", options=["AND", "OR"], horizontal=True)

    all_genres = sorted({genre for track in tracks for genre in track.get("genres", [])})
    selected_genres = st.sidebar.multiselect("Genres", options=all_genres)

    all_artists = sorted({artist for track in tracks for artist in track.get("artist_names", [])})
    selected_artists = st.sidebar.multiselect("Artists", options=all_artists)

    year_bounds = _compute_year_bounds(tracks)
    year_range = st.sidebar.slider(
        "Release year",
        min_value=year_bounds[0],
        max_value=year_bounds[1],
        value=year_bounds,
        step=1,
    )

    audio_bounds = _compute_audio_bounds(tracks)
    audio_ranges: dict[str, tuple[float, float]] = {}

    if audio_available:
        st.sidebar.subheader("Audio features")
        for key in AUDIO_FEATURE_KEYS:
            low, high = audio_bounds[key]
            if key == "tempo":
                audio_ranges[key] = st.sidebar.slider(
                    AUDIO_FEATURE_LABELS[key],
                    min_value=float(low),
                    max_value=float(high),
                    value=(float(low), float(high)),
                    step=1.0,
                )
            else:
                audio_ranges[key] = st.sidebar.slider(
                    AUDIO_FEATURE_LABELS[key],
                    min_value=0.0,
                    max_value=1.0,
                    value=(float(low), float(high)),
                    step=0.01,
                )
    else:
        st.sidebar.info("Audio features unavailable for current data source.")

    return FilterConfig(
        mode=mode,
        selected_genres=selected_genres,
        selected_artists=selected_artists,
        year_range=year_range,
        year_bounds=year_bounds,
        audio_ranges=audio_ranges,
        audio_bounds=audio_bounds,
    )


def main() -> None:
    try:
        auth_manager = create_auth_manager()
    except AuthConfigurationError as exc:
        st.error(str(exc))
        st.stop()

    session = _render_auth(auth_manager)
    if not session:
        st.stop()
    missing_scopes = get_missing_scopes(session.token_info)
    write_scope_missing = "playlist-modify-private" in missing_scopes

    st.sidebar.header("Playlist")
    refresh_from_spotify = st.sidebar.button("Refresh from Spotify")

    if refresh_from_spotify or "playlists" not in st.session_state:
        with st.spinner("Loading playlists from Spotify..."):
            try:
                playlists = fetch_all_playlists(session.client)
                liked_songs_total = fetch_saved_tracks_total(session.client)
                st.session_state["playlists"] = playlists
                st.session_state["liked_songs_total"] = liked_songs_total
                cache_store.touch_global_refresh()
            except (SpotifyAPIError, Exception) as exc:  # noqa: BLE001
                st.error(f"Failed to load playlists: {exc}")
                st.stop()

    playlists = st.session_state.get("playlists", [])
    liked_songs_total = int(st.session_state.get("liked_songs_total", 0) or 0)

    playlist_entries = build_playlist_select_options(playlists)
    liked_entry = (LIKED_SONGS_SOURCE_ID, f"{LIKED_SONGS_SOURCE_NAME} ({liked_songs_total} tracks)")
    all_entries = [liked_entry] + playlist_entries

    if not all_entries:
        st.warning("No playlists or liked songs found on this account.")
        st.stop()

    playlist_ids = [playlist_id for playlist_id, _ in all_entries]
    playlist_options = {playlist_id: label for playlist_id, label in all_entries}

    selected_playlist_id = st.sidebar.selectbox(
        "Select playlist",
        options=playlist_ids,
        format_func=lambda playlist_id: playlist_options.get(playlist_id, playlist_id),
    )

    if not selected_playlist_id:
        st.stop()

    selected_playlist_name = playlist_options[selected_playlist_id]

    with st.spinner("Loading playlist tracks and metadata..."):
        try:
            tracks, audio_available, _audio_warning, fetched_at, source = _get_playlist_data(
                session,
                selected_playlist_id,
                force_refresh=refresh_from_spotify,
            )
        except (SpotifyAPIError, Exception) as exc:  # noqa: BLE001
            st.error(f"Failed to load playlist data: {exc}")
            st.stop()

    if not tracks:
        st.warning("No playable tracks found in this playlist.")
        st.stop()

    st.sidebar.caption(
        f"Data source: {source} | Last refresh: {cache_store.format_age(fetched_at)}"
    )

    if not audio_available:
        st.info("Audio feature filters are disabled because Spotify audio feature data is unavailable.")

    filter_config = _render_filter_controls(tracks, audio_available)
    filtered_tracks = apply_filters(tracks, filter_config)

    st.subheader(selected_playlist_name)
    tab_explore, tab_analytics, tab_automations = st.tabs(["Explore", "Analytics", "Automations"])

    with tab_explore:
        metric_col1, metric_col2, metric_col3 = st.columns(3)
        metric_col1.metric("Total tracks", len(tracks))
        metric_col2.metric("Matched tracks", len(filtered_tracks))
        metric_col3.metric("Audio filters", "Enabled" if audio_available else "Disabled")

        st.markdown("### Genre Distribution (Top 10 in Playlist)")
        genre_rows = build_genre_count_rows(tracks, total_tracks=len(tracks), top_n=10)
        if genre_rows:
            genre_df = pd.DataFrame(genre_rows)
            genre_df["label"] = genre_df.apply(
                lambda row: f"{int(row['track_count'])} ({int(round(float(row['share_pct'])))}%)",
                axis=1,
            )
            genre_order = genre_df["genre"].tolist()
            max_count = int(max(genre_df["track_count"])) if not genre_df.empty else 1
            x_domain_max = max_count + max(1, int(round(max_count * 0.25)))

            bar_base = alt.Chart(genre_df).encode(
                y=alt.Y("genre:N", sort=genre_order, title="Genre"),
                x=alt.X(
                    "track_count:Q",
                    title="Track count",
                    scale=alt.Scale(domain=[0, x_domain_max]),
                ),
                tooltip=[
                    alt.Tooltip("genre:N", title="Genre"),
                    alt.Tooltip("track_count:Q", title="Tracks", format=".0f"),
                    alt.Tooltip("share_pct:Q", title="Share of playlist (%)", format=".2f"),
                ],
            )
            chart = (
                bar_base.mark_bar(color="#1DB954")
                + bar_base.mark_text(align="left", baseline="middle", dx=4, color="#111827").encode(
                    text="label:N"
                )
            ).properties(width=760, height=360)

            # Static chart: fixed dimensions, no interactive zoom/pan.
            st.altair_chart(chart, use_container_width=False)
        else:
            st.info("No genre data available for this playlist.")

        if not filtered_tracks:
            st.info("No tracks matched your current filters. Try widening ranges or switching AND/OR mode.")
        else:
            st.markdown("### Filtered Tracks")
            dataframe = filtered_tracks_to_dataframe(filtered_tracks)
            dataframe_display = dataframe.drop(
                columns=["relevance_score", "match_reasons", "spotify_url"],
                errors="ignore",
            )
            selected_row_index: int | None = None
            try:
                table_state = st.dataframe(
                    dataframe_display,
                    use_container_width=True,
                    height=540,
                    on_select="rerun",
                    selection_mode="single-row",
                )
                selected_row_index = _extract_selected_row_index(table_state)
            except TypeError:
                st.dataframe(dataframe_display, use_container_width=True, height=540)
                st.info(
                    "Row selection playback requires a newer Streamlit version. "
                    "Upgrade Streamlit to enable click-to-play from table rows."
                )

            st.markdown("### Now Playing")
            if selected_row_index is None:
                st.info("Select a row in the table above to play that track.")
            elif selected_row_index >= len(filtered_tracks):
                st.info("Selected row is no longer available after filtering. Select a new row to play.")
            else:
                selected_track = filtered_tracks[selected_row_index]
                selected_track_id = selected_track.get("track_id")
                if selected_track_id:
                    embed_url = f"https://open.spotify.com/embed/track/{selected_track_id}"
                    st.markdown(
                        f"""
<iframe
    src="{embed_url}"
    width="100%"
    height="152"
    frameborder="0"
    allowfullscreen=""
    allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
    loading="lazy">
</iframe>
""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("Selected track cannot be embedded. Use the Spotify link in the results table.")

            csv_bytes = filtered_tracks_to_csv_bytes(filtered_tracks)
            safe_name = selected_playlist_name.split("(")[0].strip().replace(" ", "_") or "playlist"
            st.download_button(
                label="Download CSV",
                data=csv_bytes,
                file_name=f"{safe_name}_filtered_tracks.csv",
                mime="text/csv",
            )

    with tab_analytics:
        st.markdown("### Taste Evolution Over Time")
        st.caption(
            "For each quarter, this chart shows that quarter's top 5 genres. "
            "Share % = tracks with the genre / all tracks added in that quarter."
        )

        timeline_rows = build_genre_timeline_rows(tracks, top_n=5)
        if not timeline_rows:
            st.info(
                "Not enough time history to analyze taste evolution for this source. "
                "Try selecting Liked Songs or refreshing metadata."
            )
        else:
            timeline_df = pd.DataFrame(timeline_rows)
            summary = summarize_genre_evolution(timeline_rows)

            if summary and summary.get("latest_top_genre"):
                start_period = summary.get("start_period")
                end_period = summary.get("end_period")
                latest_genre = summary.get("latest_top_genre")
                latest_share = summary.get("latest_top_share_pct")
                rising_genre = summary.get("fastest_rising_genre")
                rising_delta = summary.get("fastest_rising_delta_pct")
                declining_genre = summary.get("fastest_declining_genre")
                declining_delta = summary.get("fastest_declining_delta_pct")

                st.info(
                    f"From {start_period} to {end_period}, your latest top genre is '{latest_genre}' "
                    f"({latest_share:.1f}%). Biggest rise: {rising_genre} "
                    f"({rising_delta:+.1f} pts). Biggest decline: {declining_genre} "
                    f"({declining_delta:+.1f} pts)."
                )

            timeline_df["label"] = timeline_df.apply(
                lambda row: f"{int(row['track_count'])} ({int(round(float(row['share_pct'])))}%)",
                axis=1,
            )
            quarter_order = (
                timeline_df.sort_values("period_index")["period"].drop_duplicates().tolist()
            )
            genre_order = (
                timeline_df.groupby("genre")["share_pct"]
                .mean()
                .sort_values(ascending=False)
                .index.tolist()
            )

            base = alt.Chart(timeline_df).encode(
                x=alt.X("period:N", sort=quarter_order, title="Quarter"),
                y=alt.Y("genre:N", sort=genre_order, title="Genre"),
                tooltip=[
                    alt.Tooltip("period:N", title="Quarter"),
                    alt.Tooltip("genre:N", title="Genre"),
                    alt.Tooltip("track_count:Q", title="Tracks", format=".0f"),
                    alt.Tooltip("share_pct:Q", title="Share (%)", format=".2f"),
                ],
            )

            heatmap = base.mark_rect(cornerRadius=3).encode(
                color=alt.Color(
                    "share_pct:Q",
                    title="Share (%)",
                    scale=alt.Scale(
                        domain=[0, 100],
                        range=["#f8fafc", "#bbf7d0", "#22c55e", "#14532d"],
                    ),
                )
            )
            labels = base.mark_text(color="#0f172a", fontSize=10).encode(text="label:N")

            chart_height = max(420, len(genre_order) * 36)
            heatmap_chart = (heatmap + labels).properties(width=1300, height=chart_height)
            st.altair_chart(heatmap_chart, use_container_width=True)

    with tab_automations:
        st.markdown("### Automated Playlists")
        st.caption(
            "Create genre-based automations and sync them manually to private Spotify playlists."
        )
        if write_scope_missing:
            st.warning(
                "Automation sync requires the `playlist-modify-private` scope. "
                "Click Reconnect in the sidebar to re-authorize."
            )

        pending_confirmations = st.session_state.setdefault("pending_clear_confirmations", {})
        summary_message = st.session_state.pop("automation_sync_summary", None)
        if summary_message:
            st.info(summary_message)

        automations = automation_store.list_automations()
        automation_genres = {
            genre
            for rule in automations
            for genre in rule.get("genres", [])
            if isinstance(genre, str) and genre.strip()
        }
        automation_genres.update(
            genre
            for track in tracks
            for genre in track.get("genres", [])
            if isinstance(genre, str) and genre.strip()
        )
        genre_options = sorted(automation_genres)

        st.markdown("#### Create Automation")
        create_name = st.text_input("Automation name", value="", key="create_automation_name")
        create_source_type = st.radio(
            "Source",
            options=["liked_songs", "playlist"],
            format_func=lambda value: "Liked Songs" if value == "liked_songs" else "Playlist",
            horizontal=True,
            key="create_automation_source_type",
        )

        source_playlist_id: str | None = None
        source_label = "Liked Songs"
        if create_source_type == "playlist":
            selectable_playlist_ids = [playlist_id for playlist_id, _ in playlist_entries]
            playlist_label_map = {playlist_id: label for playlist_id, label in playlist_entries}
            if selectable_playlist_ids:
                source_playlist_id = st.selectbox(
                    "Source playlist",
                    options=selectable_playlist_ids,
                    format_func=lambda playlist_id: playlist_label_map.get(playlist_id, playlist_id),
                    key="create_automation_source_playlist_id",
                )
                source_label = playlist_label_map.get(source_playlist_id, source_playlist_id)
            else:
                st.warning("No playlists available as source. Refresh playlists from sidebar first.")

        create_genres = st.multiselect(
            "Genres",
            options=genre_options,
            key="create_automation_genres",
        )
        create_genre_mode = st.radio(
            "Genre mode",
            options=["OR", "AND"],
            horizontal=True,
            key="create_automation_genre_mode",
        )
        default_target_name = create_name.strip() or "Automated Playlist"
        create_target_name = st.text_input(
            "Target playlist name (optional)",
            value="",
            placeholder=default_target_name,
            key="create_automation_target_name",
        )
        create_submitted = st.button("Create automation", key="create_automation_submit")

        if create_submitted:
            if not create_name.strip():
                st.error("Automation name is required.")
            elif not create_genres:
                st.error("Select at least one genre.")
            elif create_source_type == "playlist" and not source_playlist_id:
                st.error("Select a source playlist.")
            else:
                created = automation_store.create_automation(
                    {
                        "name": create_name.strip(),
                        "source_type": create_source_type,
                        "source_playlist_id": source_playlist_id,
                        "source_label": source_label,
                        "target_playlist_id": None,
                        "target_playlist_name": create_target_name.strip() or default_target_name,
                        "privacy": "private",
                        "genres": create_genres,
                        "genre_mode": create_genre_mode,
                        "order": "added_desc",
                        "no_match_policy": "confirm_clear",
                    }
                )
                for key in (
                    "create_automation_name",
                    "create_automation_source_type",
                    "create_automation_source_playlist_id",
                    "create_automation_genres",
                    "create_automation_genre_mode",
                    "create_automation_target_name",
                ):
                    st.session_state.pop(key, None)
                st.session_state["automation_sync_summary"] = (
                    f"Automation '{created['name']}' created."
                )
                st.rerun()

        st.markdown("#### Existing Automations")
        if not automations:
            st.info("No automations yet. Create one above.")
        else:
            col_sync_all, _ = st.columns([1, 3])
            if col_sync_all.button("Sync All", disabled=write_scope_missing):
                success_count = 0
                error_count = 0
                confirm_count = 0
                for rule in automations:
                    updated_rule, result = sync_automation_rule(
                        sp=session.client,
                        cache_store=cache_store,
                        rule=rule,
                        allow_empty_clear=False,
                    )
                    automation_store.update_automation(rule["id"], updated_rule)
                    if result.get("requires_clear_confirmation"):
                        pending_confirmations[rule["id"]] = True
                        confirm_count += 1
                    elif result.get("status") == "success":
                        success_count += 1
                        pending_confirmations.pop(rule["id"], None)
                    else:
                        error_count += 1
                st.session_state["pending_clear_confirmations"] = pending_confirmations
                st.session_state["automation_sync_summary"] = (
                    f"Sync All finished: {success_count} success, {error_count} errors, "
                    f"{confirm_count} awaiting clear confirmation."
                )
                st.rerun()

            for rule in automations:
                automation_id = str(rule.get("id"))
                rule_name = str(rule.get("name", "Untitled automation"))
                source_label = str(rule.get("source_label", "Liked Songs"))
                target_name = str(rule.get("target_playlist_name", "Auto playlist"))
                rule_status = (rule.get("last_sync_result") or {}).get("status", "never")
                rule_message = (rule.get("last_sync_result") or {}).get("message", "Not synced yet.")
                last_sync_at = rule.get("last_sync_at")

                with st.container(border=True):
                    st.markdown(f"**{rule_name}**")
                    st.caption(
                        f"Source: {source_label} | Genres: {', '.join(rule.get('genres', []))} "
                        f"| Mode: {rule.get('genre_mode', 'OR')} | Target: {target_name}"
                    )
                    st.caption(
                        f"Last sync: {cache_store.format_age(last_sync_at) if last_sync_at else 'never'} "
                        f"| Status: {rule_status}"
                    )
                    st.write(rule_message)

                    action_col1, action_col2, action_col3 = st.columns([1, 1, 1])
                    if action_col1.button("Sync", key=f"sync_rule_{automation_id}", disabled=write_scope_missing):
                        updated_rule, result = sync_automation_rule(
                            sp=session.client,
                            cache_store=cache_store,
                            rule=rule,
                            allow_empty_clear=False,
                        )
                        automation_store.update_automation(automation_id, updated_rule)
                        if result.get("requires_clear_confirmation"):
                            pending_confirmations[automation_id] = True
                            st.session_state["automation_sync_summary"] = (
                                f"Rule '{rule_name}' has no matches. Confirm clear to empty target playlist."
                            )
                        else:
                            pending_confirmations.pop(automation_id, None)
                            st.session_state["automation_sync_summary"] = result.get("message")
                        st.session_state["pending_clear_confirmations"] = pending_confirmations
                        st.rerun()

                    if action_col2.button("Delete", key=f"delete_rule_{automation_id}"):
                        automation_store.delete_automation(automation_id)
                        pending_confirmations.pop(automation_id, None)
                        st.session_state["pending_clear_confirmations"] = pending_confirmations
                        st.session_state["automation_sync_summary"] = f"Rule '{rule_name}' deleted."
                        st.rerun()

                    with st.expander("Edit rule"):
                        with st.form(f"edit_rule_form_{automation_id}"):
                            edit_name = st.text_input("Automation name", value=rule_name)
                            edit_source_type = st.radio(
                                "Source",
                                options=["liked_songs", "playlist"],
                                format_func=lambda value: (
                                    "Liked Songs" if value == "liked_songs" else "Playlist"
                                ),
                                index=0 if rule.get("source_type") == "liked_songs" else 1,
                                horizontal=True,
                            )
                            edit_source_playlist_id = None
                            edit_source_label = "Liked Songs"
                            if edit_source_type == "playlist":
                                selectable_playlist_ids = [playlist_id for playlist_id, _ in playlist_entries]
                                playlist_label_map = {
                                    playlist_id: label for playlist_id, label in playlist_entries
                                }
                                current_source_id = rule.get("source_playlist_id")
                                if (
                                    current_source_id
                                    and current_source_id not in selectable_playlist_ids
                                ):
                                    selectable_playlist_ids = [current_source_id] + selectable_playlist_ids
                                    playlist_label_map[current_source_id] = (
                                        f"Unavailable source ({current_source_id})"
                                    )
                                if selectable_playlist_ids:
                                    default_index = (
                                        selectable_playlist_ids.index(current_source_id)
                                        if current_source_id in selectable_playlist_ids
                                        else 0
                                    )
                                    edit_source_playlist_id = st.selectbox(
                                        "Source playlist",
                                        options=selectable_playlist_ids,
                                        index=default_index,
                                        format_func=lambda playlist_id: playlist_label_map.get(
                                            playlist_id, playlist_id
                                        ),
                                    )
                                    edit_source_label = playlist_label_map.get(
                                        edit_source_playlist_id, str(edit_source_playlist_id)
                                    )
                                else:
                                    st.warning("No playlists available as source.")

                            edit_genres = st.multiselect(
                                "Genres",
                                options=genre_options,
                                default=rule.get("genres", []),
                                key=f"edit_genres_{automation_id}",
                            )
                            edit_genre_mode = st.radio(
                                "Genre mode",
                                options=["OR", "AND"],
                                index=0 if str(rule.get("genre_mode", "OR")).upper() == "OR" else 1,
                                horizontal=True,
                                key=f"edit_mode_{automation_id}",
                            )
                            edit_target_name = st.text_input(
                                "Target playlist name",
                                value=target_name,
                                key=f"edit_target_{automation_id}",
                            )
                            edit_submitted = st.form_submit_button("Save changes")

                            if edit_submitted:
                                if not edit_name.strip():
                                    st.error("Automation name is required.")
                                elif not edit_genres:
                                    st.error("Select at least one genre.")
                                elif edit_source_type == "playlist" and not edit_source_playlist_id:
                                    st.error("Select a source playlist.")
                                else:
                                    automation_store.update_automation(
                                        automation_id,
                                        {
                                            "name": edit_name.strip(),
                                            "source_type": edit_source_type,
                                            "source_playlist_id": edit_source_playlist_id,
                                            "source_label": edit_source_label,
                                            "genres": edit_genres,
                                            "genre_mode": edit_genre_mode,
                                            "target_playlist_name": edit_target_name.strip() or edit_name.strip(),
                                        },
                                    )
                                    st.session_state["automation_sync_summary"] = (
                                        f"Rule '{edit_name.strip()}' updated."
                                    )
                                    st.rerun()

                    if pending_confirmations.get(automation_id):
                        confirm_col, cancel_col = st.columns([1, 1])
                        if confirm_col.button(
                            "Confirm clear and sync",
                            key=f"confirm_clear_{automation_id}",
                            disabled=write_scope_missing,
                        ):
                            updated_rule, result = sync_automation_rule(
                                sp=session.client,
                                cache_store=cache_store,
                                rule=rule,
                                allow_empty_clear=True,
                            )
                            automation_store.update_automation(automation_id, updated_rule)
                            pending_confirmations.pop(automation_id, None)
                            st.session_state["pending_clear_confirmations"] = pending_confirmations
                            st.session_state["automation_sync_summary"] = result.get("message")
                            st.rerun()
                        if cancel_col.button("Cancel clear", key=f"cancel_clear_{automation_id}"):
                            pending_confirmations.pop(automation_id, None)
                            st.session_state["pending_clear_confirmations"] = pending_confirmations
                            st.session_state["automation_sync_summary"] = (
                                f"Clear confirmation cancelled for '{rule_name}'."
                            )
                            st.rerun()


if __name__ == "__main__":
    main()
