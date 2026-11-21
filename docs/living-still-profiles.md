# Living Still — output profiles (P1b/P2)

Mục tiêu: profile 16:9, 9:16, 1:1, 4:5; dùng một profile cho mỗi lần xuất. Chưa làm
xuất nhiều profile cùng lúc (2×N video).

## Profiles

```text
landscape_1080p
  size 1920×1080, fps 30
  fit_mode contain
  strip_rect (0, 800, 1920, 280)
  safe_top 0, safe_bottom 0, safe_left 0, safe_right 0
  lyric_font_size 56, max_lyric_width 1700, lyric_row_gap 10
  cover_title 78, cover_artist 40, cover_line 30, cover_max_width 1500

portrait_1080x1920
  size 1080×1920, fps 30
  fit_mode contain
  strip_rect (0, 1333, 1080, 280)   # đáy strip 1613, chừa 307 px safe bottom
  safe_top 173, safe_bottom 307, safe_left 0, safe_right 118
  lyric_font_size 48, max_lyric_width 960, lyric_row_gap 8
  cover_title 64, cover_artist 34, cover_line 26, cover_max_width 900

square_1080
  size 1080×1080, fps 30
  fit_mode contain
  strip_rect (0, 720, 1080, 280)    # đáy strip 1000, chừa 80 px
  safe_top 0, safe_bottom 0, safe_left 0, safe_right 0
  lyric_font_size 44, max_lyric_width 960, lyric_row_gap 8
  cover_title 56, cover_artist 30, cover_line 24, cover_max_width 900

portrait_4x5
  size 1080×1350, fps 30
  fit_mode contain
  strip_rect (0, 990, 1080, 280)    # đáy strip 1270, chừa 80 px
  safe_top 0, safe_bottom 0, safe_left 0, safe_right 0
  lyric_font_size 44, max_lyric_width 960, lyric_row_gap 8
  cover_title 58, cover_artist 32, cover_line 24, cover_max_width 900
```

- `strip_rect` là vị trí lyric strip. Effect render **trước** lyric overlay nên có
  thể chạy phía sau; text/bar bán trong suốt vẽ sau đảm bảo đọc được.
- P1b chỉ hỗ trợ `contain` và `cover`; `blur-fill` để P2.
- `output_profile` lưu trong project; default `landscape_1080p`.
- Unknown profile -> fallback `landscape_1080p`.

## API core

`karaoke/profiles.py` (new):

```python
@dataclass(frozen=True)
class RenderProfile:
    id: str
    width: int
    height: int
    fps: int
    fit_mode: str
    strip_rect: tuple[int, int, int, int]
    safe_top: int
    safe_bottom: int
    safe_left: int
    safe_right: int
    lyric_font_size: int
    max_lyric_width: int
    lyric_row_gap: int
    cover_title_size: int
    cover_artist_size: int
    cover_line_size: int
    cover_max_width: int

PROFILES: dict[str, RenderProfile]
def get_profile(profile_id: str | None) -> RenderProfile
def normalize_profile_id(profile_id: str | None) -> str
```

`media.py`:
- `fit_image(source, size=(WIDTH, HEIGHT), mode="contain")`; keep default behavior.
  - contain = current.
  - cover = ImageOps.fit to size (crop).
- `prepare_slide(source, destination, profile=None)`.
- `make_cover(photo, title, artist, cover_line, destination, profile=None)`.
- Keep `WIDTH/HEIGHT/STRIP_HEIGHT` constants for old tests/defaults.

`render.py`:
- `render_videos(..., output_profile: str = "landscape_1080p")`.
- `_render_text_pair(line, profile=None)`, `_draw_strip(layout, song_time, profile=None)`,
  `_draw_countdown(layout, number, profile=None)`, `_frame_at(..., profile=None)`,
  `_overlay_text(..., profile=None)`. Default = landscape profile.
- Use `profile.width/height/strip_rect/lyric_font_size/max_lyric_width/lyric_row_gap`.
- slideshow scale `profile.width:profile.height`.
- `make_cover`/`prepare_slide` receive profile.
- `render_motion_loop(..., width=profile.width, height=profile.height,
  fit_mode=profile.fit_mode)`.
- Export names: add optional profile suffix for portrait, e.g. `(dọc)`. Keep function
  backward compatible with 3 args.

`pipeline.py`:
- `prepare_job` normalize `output_profile`.
- `render_job` pass `output_profile` to `render_videos` and to `unique_export_names`.
- `project.py` validate/normalize `output_profile`.

`living.py`:
- `render_motion_loop(..., fit_mode="contain")`.
- Base = `fit_image(source, (out_width, out_height), mode=fit_mode)`; do NOT fit to
  1920×1080 then resize (that distorts 9:16).
- `_fit_transform` recomputes from source size + output frame + fit_mode. Do not trust
  the landscape `plan.fit_transform` when output size differs.
- Cache key includes `fit_mode`.

## Frontend

- Add a select **“Định dạng video”** in Khai báo (section Hình or a small new block):
  - `landscape_1080p` — 1920×1080 (16:9)
  - `portrait_1080x1920` — 1080×1920 (9:16, TikTok/Reels/Shorts)
- Update `fields.output_profile`; already in `ProjectFields`/`fieldsPayload`.
- `AiEffects` shows the selected profile and a note:
  - 9:16: lyric strip is above the bottom safe area; top/right safe areas are for app UI.
  - Keep the analysis overlay on the original image for P1b; the actual render fits the
    profile. Do not claim the overlay is the final 9:16 crop.
- `npx tsc --noEmit` and `npm run build` must pass.

## Smoke tests

- 16:9: render 1 s with synthetic audio; output 1920×1080.
- 9:16: render 1 s with synthetic audio; output 1080×1920; text nằm trong safe area; no text overflow.
- Ảnh 16:9 -> 9:16 contain; ảnh 9:16 -> 16:9 contain.
- Existing 80 tests must stay green.
