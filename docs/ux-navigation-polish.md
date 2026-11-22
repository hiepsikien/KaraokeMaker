# UX & Navigation Polish — Spec v2

Trạng thái: **Đã triển khai phần lớn P0 + một phần P1**
Ngày: 2026-11-22
Thay thế: bản v1 của chính file này (đã sửa theo review kỹ thuật)
Liên quan:

- `web/src/app/layout.tsx`, `web/src/app/page.tsx`, `web/src/app/studio/page.tsx`
- `web/src/components/maker-form.tsx`
- `web/src/components/project-setup.tsx`
- `web/src/components/timing-editor.tsx`
- `web/src/components/studio-workspace.tsx`
- `web/src/components/ai-effects.tsx`
- `web/src/components/image-cards.tsx`
- `karaoke/server.py`, `karaoke/assets.py`, `karaoke/project.py`, `karaoke/studio.py`
- `docs/background-performance-ux-proposal.md` (doc cũ, vẫn có hiệu lực ở các điều kiện an toàn)
- `docs/living-still-interfaces.md`

---

## 0. Quyết định & câu hỏi blocking

Các quyết định dưới đây chốt trước khi code. Cột `Blocking` = có chặn P0 hay không.

| ID | Quyết định | Blocking | Ghi chú |
|---|---|---|---|
| **D1** | **Chưa làm left rail 240px trong P0/P1.** Dùng top bar + segmented `Dự án \| AI Studio` + status toàn cục. Chỉ làm rail khi có ≥3 destination toàn cục thật (Dự án, Studio, Thư viện) **và** đã xong persistence. Nếu vẫn muốn rail sớm: icon rail 72px, không phải 240px. | Có | Tránh tầng nav trùng tab Studio và ăn chỗ canvas ở 1280px |
| **D2** | Route: `/` = dashboard; `/project?project=<path-encoded>` = workspace (canonical); `?job=` chỉ là con trỏ tạm, không phải nguồn sự thật. Studio: `/studio`, `/studio?tab=library`, `?asset=<id>`. | Có | Path-encoded đủ cho app local; có thể đổi sang `id` hash sau mà không phá API |
| **D3** | Stepper còn **2 bước**: `Chuẩn bị · Biên tập`. `Xuất` là **drawer + status chip** (`Chưa xuất / Đang xuất / Đã xuất`), không phải màn riêng. | Có | Giải quyết mâu thuẫn “Xuất là bước hay trạng thái” |
| **D4** | State ownership: server/project file là **nguồn sự thật bền**; global store là **phiên đang làm**; `sessionStorage` chỉ giữ **con trỏ + draft chưa lưu** (có version, có TTL). | Có | Không coi `sessionStorage` là lớp bảo vệ duy nhất |
| **D5** | Clip: `Xem nhanh` = 640px, **không lưu được**; `Tạo clip chuẩn` = full-res, **lưu được**. Auto-save hoãn đến khi có upsert + eviction + reference guard. | Có | Phản ánh đúng `save_asset_from_preview` chỉ nhận preview full-res |
| **D6** | Xoá asset: guard reference **cùng PR** với nút Xoá; cleanup có `dry_run`; khuyến nghị soft-delete/trash. | Có | Hiện `delete_asset` không kiểm tra reference |
| **D7** | Lỗi có 3 tầng: **field inline** (checklist setup), **action toast** (lưu/xoá/rename), **job status bar** (align/render). Không gộp tất cả thành “một banner”. | Có | Checklist và banner không thay thế nhau |
| **D8** | Align xong vẫn auto-mở Biên tập ở P0, nhưng thêm notice + log; chỉ đổi sang “toast + Mở biên tập” khi có global job store. | Không | Giữ hành vi cũ để giảm rủi ro; tránh tự ý đổi flow |
| **D9** | Settings là **drawer**, chưa phải route. Rail (nếu có) không có mục Cài đặt. | Không | App local 2 màn hình, chưa đủ ≥3 nhóm setting |
| **D10** | A11y nằm trong primitive ngay P0: focus trap, `Esc`, trả focus, `aria-live`, `role=tablist`, `aria-current`. Không cam kết full keyboard cho thao tác vẽ object. | Không | Drawer/dialog xuất hiện ngay P0 |
| **D11** | Mobile phone **non-goal**. Target là cửa sổ hẹp 1024–1280 trên macOS; test 1280×800 và 1440×900. | Không | Hộp thoại file dùng AppleScript/Finder, điện thoại không dùng được |
| **D12** | Trích dẫn trong spec dùng **tên hàm/component**, không dùng số dòng. | Không | Số dòng sẽ lệch sau mỗi PR |

Nếu chưa chốt D1–D6 thì **không nên code P0**.

---

## 1. Non-goals (đợt này)

- Không làm left rail 240px; không làm command palette `⌘K`.
- Không làm route `/settings`; settings là drawer.
- Không làm mobile phone / bottom tab bar.
- Không viết lại renderer hiệu ứng, pipeline background hay timing algorithm.
- Không auto-save asset trước khi có upsert + eviction + reference guard.
- Không đổi `image_duration` toàn cục theo asset (giữ điều kiện của doc cũ).
- Không bỏ `effect_consent_receipt`; consent chỉ chuyển chỗ nhập.
- Không hứa Back theo từng bước trong workspace (xem D2/D3).
- Không bulk-select/bulk-delete asset trong đợt này.
- Không redesign branding/màu sắc; giữ palette hiện tại.

---

## 2. Baseline & vấn đề

### 2.1. Cách đo baseline

Đo trước P0, trên fixture: 1 dự án 12 câu, 3 ảnh nền, 1 dự án đã xuất, 12 asset (4 profile).

| Metric | Cách đo | Hiện tại | Mục tiêu |
|---|---|---|---|
| Chiều cao màn Chuẩn bị | `form.scrollHeight` ở 1280×800 | **TBD** | Giảm ≥25% ở P0, ≥40% sau khi đưa hiệu ứng vào drawer |
| Chiều cao tab Biên tập Studio | `main.scrollHeight` ở 1280×800 | **TBD** | Không scroll dọc ở trạng thái mặc định |
| Bề rộng stage Studio | `stage.getBoundingClientRect().width` ở 1280 | **TBD** | ≥520px khi không rail; ≥420px nếu sau này có icon rail |
| Số bề mặt thông báo | đếm theo code | **10** (`notice`, `error`, `assetsError`, `inspectError`, `renderHint`, `analyzeHint`, help card, portrait note, capability note, `saveError`) | 1 action toast + 1 job status + field inline |
| Số click tới clip đầu tiên | từ ảnh đã chọn + plan đã áp dụng | 4–6 (Preview → đợi → Bản chuẩn → đợi → prompt tên → Lưu) | ≤4; không `window.prompt` |
| Số lần gọi `/api/studio/assets` khi mở Studio | network log | 2 (Studio + AiEffects) | 1 (hook dùng chung), summary payload |
| Payload `/api/studio/assets` | `curl \| wc -c` với 50 asset | **TBD** (full `effect_plan`) | summary < 100KB; detail fetch khi mở |
| Số asset trùng sau 5 lần chỉnh slider + lưu | thực nghiệm | 5 bản mới | 0 nếu bật auto-save; 1 bản/lần lưu thủ công |

Các số `TBD` phải điền bằng đo thật trước khi coi §9 là tiêu chí nghiệm thu. Không dùng
“~180–220px” hay “giảm 40–50%” làm số liệu chính thức.

### 2.2. Danh sách vấn đề có ID

| ID | Vấn đề | Evidence (symbol) |
|---|---|---|
| **U1** | Mất việc khi đổi route: state nằm trong component, unmount là mất. Nặng nhất là `TimingEditor` giữ `lines`, `dirty`, `removed`, `selectedLine`, `exportDir`. | `MakerForm`, `StudioWorkspace`, `TimingEditor` local state |
| **U2** | Studio trộn 2 nhiệm vụ trong 1 scroll: editor rất dài, thư viện nằm cuối. | `StudioWorkspace` render; `saveAsset`/`openAsset` scroll |
| **U3** | Thư viện thiếu search/filter/sort/list/delete; card cao, 3 cột, metadata xếp dọc. | `StudioWorkspace` asset grid |
| **U4** | Object/effect một dòng một item, scroll lồng `max-h-64`; inspector hẹp không đủ 2 cột. | `AiEffects` object list & params |
| **U5** | Thông báo phân mảnh; `notice` success render nền đỏ. | `StudioWorkspace` notice, `ProjectSetup` notice, `TimingEditor` error |
| **U6** | Hai nơi fetch asset; list trả full `effect_plan`. | `StudioWorkspace.loadAssets`, `AiEffects.refreshAssets`, `assets.list_assets` |
| **U7** | Xoá asset không guard reference; auto-save chưa có sẽ tạo bản trùng; cleanup xoá ngay, auto-cleanup bỏ qua asset <30 ngày. | `assets.delete_asset`, `save_asset_from_preview`, `server.studio_asset_create`, `cleanup_assets` |
| **U8** | Setup quá dài; `AiEffects` nhúng nguyên khối dù tuỳ chọn; card “Luồng” lặp stepper. | `ProjectSetup` |
| **U9** | Biên tập: panel Xuất + log chiếm chỗ thường trực; câu nhạc luôn mở; nhiều copy dài. | `TimingEditor` |
| **U10** | Route/back/deep-link chưa định nghĩa; không refresh được giữa chừng. | không có `useSearchParams`, không có route `/project` |
| **U11** | “Tạo clip” chưa rõ: preview 640px không lưu được, full-res mới lưu; 2 CTA ngang hàng. | `studio.render_preview`, `assets.save_asset_from_preview` |
| **U12** | Nút Huỷ render không có backend; `AbortController` chỉ ngắt fetch client. | `server` không có cancel endpoint |
| **U13** | Không có dự án gần đây dù server đã có `.projects.json`. | `assets.known_project_paths`, `HomeGate` |
| **U14** | Card asset dùng `aspect-video` + `object-cover` cho mọi profile → clip 9:16/1:1 bị crop. | `StudioWorkspace` asset video |
| **U15** | Naming lệch: `Karaoke` / `Studio` / `Workspace` / `AI Studio`. | `layout.tsx`, page headers |
| **U16** | Không có job status toàn cục; rời trang là polling dừng. | polling trong `MakerForm`, `renderStandard` |
| **U17** | A11y dialog/drawer chưa có; canvas vẽ chỉ dùng chuột. | `PlanOverlay` |
| **U18** | Server offline chỉ báo theo từng action, ~20 chuỗi khác nhau. | các `catch` trong `web/src` |
| **U19** | Search tiếng Việt (khi thêm) phải accent-insensitive; hiện chưa có search. | — |
| **U20** | Chưa có thumbnail video; `media.py` chỉ thumbnail ảnh. | `media._data_url`/`inspect_image` |

---

## 3. State & data model

### 3.1. Nguồn sự thật

| Dữ liệu | Bền (durable) | Phiên (transient) | Owner |
|---|---|---|---|
| Project fields | file `.karaoke.json` | global store | `WorkspaceProvider` |
| Job (lines, status, log) | server `_jobs` (in-memory, prune) | global `JobStore` | `JobStore` |
| Timing đang sửa | draft trong `sessionStorage` (debounce) | editor store | `useProjectEditor` |
| Studio draft (ảnh, plan, objects) | chỉ `sessionStorage` draft, không bền | `StudioDraftStore` | `StudioEditor` |
| Asset đã lưu | `data_root/assets/<id>` | — | server |
| Settings (consent, device) | app-data JSON | — | server (P2) |

**Precedence khi hydrate:**

1. `?job=` và job còn sống → dùng job.
2. `?project=` → mở project file.
3. `sessionStorage` pointer → thử job trước, fallback project.
4. Nếu có draft mới hơn project file → hỏi `Khôi phục bản nháp?` (không tự ghi đè).
5. Không có gì → `/` dashboard.

Job id **không** phải nguồn sự thật: server restart là 404. Vì vậy `?project=` mới là
canonical, `?job=` chỉ để giữ đúng phiên đang chạy.

### 3.2. Dirty registry & guard

- `WorkspaceProvider` giữ `Map<key, () => boolean>` các nguồn dirty:
  `project`, `timing`, `studioDraft`.
- Nav item/top bar dùng `guardedNavigate(href)`:
  - nếu không dirty → đi;
  - nếu dirty → `ConfirmDialog`: `Lưu rồi rời` / `Rời không lưu` / `Ở lại`.
- `beforeunload` cho reload/đóng tab.
- **Giới hạn đã biết**: App Router không có navigation guard chính thức; Back của trình duyệt
  không chặn được đầy đủ nếu không thêm sentinel history. Vì vậy:
  - draft timing autosave `sessionStorage` mỗi 1–2s (không phải server save);
  - tài liệu ghi rõ Back có thể rời workspace; không hứa “Back theo từng bước”.
- Không dùng `window.confirm`; dùng `ConfirmDialog` chung.

### 3.3. Editor draft (điểm dễ mất nhất)

`TimingEditor` hiện giữ `lines`, `dirty`, `removed`, `selectedLine`, `exportDir`, `time` cục bộ.
Chỉ lift `job`/`fields` là **không đủ**.

Đề xuất:

- Lift `lines`, `removed`, `dirty`, `selectedLine`, `exportDir` vào `useProjectEditor` store
  (context + reducer), key theo `projectPath`/`jobId`.
- `time`, `playing` giữ local (không cần khôi phục).
- Autosave draft vào `sessionStorage` (debounce), có `version` + `savedAt`.
- Khi mở lại: nếu draft khác bản đã lưu, hiện dialog chọn `Dùng bản nháp` / `Dùng bản đã lưu`.
- `onRender` dùng `lines` từ store, không phụ thuộc component còn mount.

### 3.4. Job store & polling

- `JobStore` giữ `{jobId → {status, percent, log, error, outputs}}`, cập nhật bằng polling
  tập trung **chỉ khi** có job active.
- Component subscribe bằng selector (`useJobStatus(jobId)`), không đưa toàn bộ object vào một
  context làm re-render cả cây.
- Polling tiếp tục khi đổi route vì provider nằm ở layout.
- Rail/header badge (nếu có) đọc từ selector này.
- Server offline: `useServerHealth` gọi `/api/health`; khi fail hiện **một** banner toàn cục
  `Chưa kết nối phần xử lý · Thử lại`, disable action nguy hiểm, không lặp 20 câu lỗi.

### 3.5. Route contract

| Route | Nội dung | History |
|---|---|---|
| `/` | Dashboard: tạo mới, mở file, dự án gần đây | — |
| `/project?project=<encoded>` | Workspace; bước Chuẩn bị/Biên tập là state | `push` từ dashboard |
| `/project?project=…&job=…` | Giữ đúng phiên đang chạy | `replace` sau khi tạo job |
| `/studio` | Editor | `push` từ nav |
| `/studio?tab=library` | Thư viện | `replace` khi toggle tab (không tạo history rác) |
| `/studio?tab=library&asset=<id>` | Mở chi tiết asset | `push`; Back quay lại thư viện |
| `/studio?asset=<id>` | Editor với asset đã nạp | `replace` sau khi nạp |

- Path encode bằng `encodeURIComponent(path)`; không đưa path vào server API mới.
- Sau này có thể thay bằng `projectId = sha256(path)[:12]` + endpoint lookup; route không đổi
  shape.
- Tab switch không dùng `router.push` để tránh mỗi lần bấm tab là một entry Back.

---

## 4. UX spec — Shell

**Top bar (D1):**

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ◆ KaraokeMaker      [ Dự án | AI Studio ]        ● Đang render 42%   ⚙      │
└──────────────────────────────────────────────────────────────────────────────┘
```

- `Dự án` / `AI Studio` là segmented, có `aria-current`.
- `⚙` mở Settings drawer, không phải route.
- Status chip chỉ hiện khi có job; click mở job panel/drawer.
- Page header bên dưới chỉ còn 1 dòng title + actions; bỏ eyebrow lặp.
- Nếu sau này làm rail: icon 72px, tooltip, **chỉ khi** D1 đã đủ điều kiện.

**Naming chốt:** `KaraokeMaker` (brand), `Dự án`, `AI Studio`, `Thư viện clip`, `Cài đặt`.

---

## 5. UX spec — AI Studio

### 5.1. IA

```text
/studio
├─ Biên tập (mặc định, giữ draft)
│   ├─ Ảnh nguồn (trái)
│   ├─ Sân khấu + prompt + action (giữa)
│   └─ Thuộc tính: Hiệu ứng / Object / Clip / Nâng cao (phải, 360px)
└─ Thư viện clip (?tab=library)
```

- Tab dùng `replaceState`; cả hai panel **keep-mounted** (`hidden` attr) để không mất state.
- Bỏ sidebar sticky phải; preview kết quả nằm trên sân khấu qua tab `Ảnh | Clip`.
- Header trang: `AI Studio` + segmented tab + nút `Trợ giúp` (popover).

### 5.2. Kích thước & responsive

- ≥1440: content 1216px; tray 240 + stage linh hoạt + inspector 360; stage ≥560px.
- 1280: tray 220 + stage ≥520 + inspector 340; nếu thiếu chỗ, tray thu còn icon rail 56px
  (trong trang, không phải global rail).
- 1024–1279: 1 cột: stage trên, inspector dưới dạng accordion; tray cuộn ngang 1 hàng.
- <1024: không phải target chính; vẫn không được tràn ngang.

### 5.3. Wireframe Biên tập

```text
┌──────────────┬────────────────────────────────────────────┬────────────────────┐
│ ẢNH NGUỒN    │ [Ảnh] [Clip]      16:9 · 8s     100%      │ THUỘC TÍNH         │
│ [+ Thêm ảnh] │  ┌──────────────────────────────────────┐  │ ▾ Hiệu ứng     3   │
│ ┌───┐┌───┐  │  │                                      │  │  Mưa    ──●────    │
│ │ ✓ ││   │  │  │        ảnh + overlay object          │  │  Gió    ──●────    │
│ └───┘└───┘  │  │                                      │  │  Khói   ──●────    │
│ ┌───┐┌───┐  │  └──────────────────────────────────────┘  │ ▸ Object     5/8   │
│ │   ││   │  │  [Mô tả hiệu ứng…              ] [Gợi ý ▾] │ ▸ Clip & asset     │
│ └───┘└───┘  │  [☑ Đồng ý gửi Gemini]  [Phân tích ảnh]     │ ▸ Nâng cao         │
│ Thiết lập ▾  │  ────────────────────────────────────────  │                    │
│              │  [Tạo clip chuẩn]  [Xem nhanh]  ● 42%      │                    │
└──────────────┴────────────────────────────────────────────┴────────────────────┘
```

- Object list: **1 cột compact** trong inspector 340–360px; chỉ dùng 2 cột nếu inspector
  ≥380px. Mỗi item: checkbox + tên + `%` badge; affordance chỉ hiện khi selected/hover.
- Effect params: mỗi effect 1 hàng; slider chính inline, tham số phụ trong expand.
- Prompt: textarea 2 dòng, quick prompts trong popover `Gợi ý ▾`.
- Consent: chỉ hiện khi chưa có setting; sau đó là chip `Gemini: đã đồng ý` + link mở Settings.
- Help text dài: popover `Trợ giúp`; dưới action bar chỉ 1 dòng trạng thái.

### 5.4. State machine

| State | Vào khi | CTA chính | CTA phụ | Invalidate khi |
|---|---|---|---|---|
| S0 Chưa ảnh | mount | `Chọn ảnh` | — | thêm ảnh → S1 |
| S1 Có ảnh, chưa phân tích | chọn ảnh | `Phân tích ảnh` (cần consent) | `Xem nhanh` (disabled nếu chưa có plan) | đổi ảnh/plan → S1 |
| S2 Đang phân tích | bấm phân tích | progress | `Huỷ` (chỉ abort client) | xong → S3 |
| S3 Có plan nháp | analysis xong | `Áp dụng` | sửa object/params | đổi ảnh → S1; đổi plan → S3 |
| S4 Đã áp dụng | Apply | `Tạo clip chuẩn` | `Xem nhanh` | đổi plan/object/profile/duration → S4 |
| S5 Có preview nhanh | quick render xong | `Tạo clip chuẩn` | `Lưu`: disabled + tooltip “cần bản chuẩn” | profile/duration/plan → S5 |
| S6 Đang render chuẩn | full job | progress, `Rời trang vẫn chạy` | — (không có Huỷ) | xong → S7; lỗi → S9 |
| S7 Có clip chuẩn | job done | `Lưu vào thư viện` | `Tải clip` | plan/profile/duration → S4 (clip stale) |
| S8 Đã lưu | save xong | `Mở trong thư viện` | `Tạo lại` | rename: không invalidate |
| S9 Lỗi | bất kỳ fail | `Thử lại` | `Xem chi tiết` | sửa input → state trước |

Quy tắc invalidate:

- Đổi `profile` hoặc `duration` → bỏ preview nhanh + clip + asset đang chọn (S5–S8 → S4).
- Đổi plan/objects → bỏ analysis draft cũ (S3) và clip (S4–S8).
- Đổi ảnh → reset toàn bộ (S1).
- Rename asset → chỉ metadata, không render lại.

### 5.5. “Mở asset” khi đang có draft

- Nếu ảnh hiện tại có thay đổi chưa lưu → dialog:
  `Ảnh này đang có thay đổi chưa lưu. Mở asset sẽ thay thế ảnh/plan hiện tại.`
  `Mở thay thế` / `Mở trong tab mới` / `Huỷ`.
- “Mở trong tab mới” dùng `window.open('/studio?asset=<id>')` (cùng server, state riêng).
- Không tự thay thế im lặng như `openAsset` hiện tại.

### 5.6. Gộp hành vi render/lưu

- `Xem nhanh`: gọi `/api/studio/render` **không** `full_resolution`; 640px; chỉ để xem.
- `Tạo clip chuẩn`: `full_resolution=true`, job nền; đây là clip duy nhất lưu được.
- Không có nút `Huỷ` cho đến khi có API cancel. Ghi rõ `Chạy nền — có thể rời trang`.
- Sau khi render xong: inline name field (mặc định tên ảnh + hậu tố nếu trùng), nút `Lưu`.
- Auto-save chỉ bật sau khi D5/D6 xong.

### 5.7. Thư viện clip

```text
AI Studio   [ Biên tập ] [ Thư viện 12 ]

┌──────────────────────────────────────────────────────────────────────────────┐
│ 🔎 Tìm…   [16:9 ▾] [Mới nhất ▾]   12 clip · 1.4 GB   [Dọn…]  [▦][☰]        │
├──────────────────────────────────────────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                             │
│ │ poster  │ │ poster  │ │ poster  │ │ poster  │                             │
│ │  8.0s   │ │  8.0s   │ │  16.0s  │ │  8.0s   │                             │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘                             │
│  Tên clip    Tên clip    Tên clip    Tên clip                               │
│  16:9 · 8s   9:16 · 8s   16:9 · 16s  1:1 · 8s                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Grid: 4 cột ≥1280, 3 cột 1024–1279, 2 cột <1024; list view cho nhiều clip.
- Toolbar: search accent-insensitive; filter profile; sort; usage (`/api/studio/assets/usage`);
  `Dọn…` mở dialog dry-run trước khi xoá.
- Card: thumbnail **đúng aspect profile** (`aspect-[16/9]`, `aspect-[9/16]`, `aspect-square`,
  `aspect-[4/5]`) + `object-contain`; tên; badge `profile · duration`; menu `⋯`.
- Video: `preload="none"`, poster JPEG, chỉ play khi hover/focus; tôn trọng
  `prefers-reduced-motion`.
- Menu `⋯`: `Mở để sửa` (`/studio?asset=`), `Đổi tên`, `Tải clip`, `Mở thư mục`, `Xoá…`.
  - `Đổi tên` cần `PATCH /api/studio/assets/{id}`.
  - `Xoá` bị chặn nếu `used`; dialog hiện `Đang dùng trong N dự án`.
  - `Mở thư mục` gọi reveal endpoint riêng, không dùng `/open`.
- Empty state: CTA `Sang Biên tập`.
- Search chuẩn hoá: `s.normalize('NFD').replace(/\p{Diacritic}/gu,'').replace(/đ/g,'d').toLowerCase()`.

---

## 6. UX spec — Karaoke

### 6.1. IA

```text
/ (Dự án)
├─ Dashboard: Tạo mới · Mở file · Dự án gần đây
└─ /project?project=…
    ├─ Chuẩn bị
    ├─ Biên tập
    └─ Xuất (drawer + status chip)
```

- Stepper 2 bước (D3); `Xuất` là status chip trên header workspace.
- Browser Back từ workspace về dashboard (D2); không có history theo bước.
- `leaveIfSaved` thay bằng `guardedNavigate` + `ConfirmDialog`.

### 6.2. Dashboard

- Trái: `Tạo dự án mới` (primary) + `Mở file .karaoke.json`.
- Phải/dưới: grid `Dự án gần đây` 4–8 item: tên bài, tác giả, status, thời gian sửa.
- Backend `GET /api/projects/recent`:
  - nguồn `.projects.json` + `known_project_paths` + quét `output_dir` mặc định;
  - dedupe theo resolved path; lọc file còn tồn tại;
  - `updated` = `st_mtime` (project JSON chưa có timestamp);
  - cap 12; cache theo `(path, mtime, size)`;
  - `cover_thumb` để P2.
- Chưa có dự án → empty state, giữ CTA hiện tại.

### 6.3. Chuẩn bị

```text
┌───────────────────────────────────────────────┬──────────────────────┐
│ Thông tin      Tên bài · Tác giả · Dòng phụ   │ Checklist            │
│ Audio          Gốc · Vocal · Instrumental     │ ✓ Audio gốc          │
│ Lời            12 câu hát                     │ ✓ 12 câu             │
│ Hình           Bìa · 1 ảnh nền  [Sửa]         │ ✓ 1 ảnh              │
│ ▸ Hiệu ứng nền (tuỳ chọn)          1 clip     │ ✓ Thư mục xuất       │
│ ▸ Tuỳ chọn xuất: thời lượng, định dạng        │ [Vào biên tập]       │
│ ▸ Nâng cao: thiết bị, model, ngưỡng           │                      │
└───────────────────────────────────────────────┴──────────────────────┘
```

- Bỏ card “Luồng”.
- `AiEffects` **không** render inline trong form. Mỗi ảnh nền có nút `Làm hiệu ứng` mở drawer
  dùng chung với Studio. Form chỉ hiện badge `1 clip · Mưa, Gió` + `Sửa`.
- Drawer phải **keep-mounted** khi đóng (hoặc lift state) để không mất `prompt/analysis/objects`
  đang làm dở.
- Giữ parity `image_duration`: truyền `imageDuration` từ project xuống Studio/drawer, không
  hard-code 16s; asset phải hợp lệ với duration của dự án.
- `Thiết bị`, `model`, `no_speech_threshold`, consent → Settings drawer + `Nâng cao`.
- `cover_duration`, `image_duration`, `output_profile` → `Tuỳ chọn xuất` (mặc định hợp lý).
- Một CTA sticky: `Vào biên tập`.
- Checklist thay cho notice dài; mỗi lỗi là field inline + link tới đúng ô.

### 6.4. Biên tập

- Danh sách câu compact, mỗi câu 1 hàng:
  `Câu 12 · 01:23.4 – 01:27.1   “lời câu hát…”   [Nghe] [⋯]`
- Click mở accordion editor chữ của đúng câu đó; chỉ câu đang chọn hiện word chips +
  `Thêm chữ` / `Chia câu`.
- `supplemented`/`uncertain` → badge nhỏ; giải thích trong tooltip.
- `Xoá câu` vào menu `⋯`; undo hiện toast `Đã xoá · Hoàn tác`.
- Panel Xuất + log + dựng nền → drawer `Xuất` bên phải (desktop) / sheet (mobile hẹp).
- Action bar dính đáy: `Đã lưu / Chưa lưu`, `Lưu`, `Xuất`.
- Progress render: 1 job status bar + % + stage; vẫn thấy khi drawer đóng.
- Kết quả: 2 file + `Mở thư mục`; lỗi có `Thử lại`.

### 6.5. Mô hình lỗi (D7)

| Tầng | Dùng cho | Ví dụ |
|---|---|---|
| Field inline | thiếu/sai input | `Thiếu vocal`, `Lời 0 câu`, `Chưa chọn thư mục` |
| Action toast | kết quả một hành động | `Đã lưu asset`, `Đã xoá 3 clip`, `Không lưu được` |
| Job status | tiến trình dài | `Đang căn lời 42%`, `Render lỗi · Thử lại` |
| Global banner | server offline | `Chưa kết nối phần xử lý · Thử lại` |

Toast có `tone: success | info | error`; success **không** dùng nền đỏ. `aria-live="polite"`
cho success/info, `assertive` cho error.

---

## 7. Design system & refactor

### 7.1. Primitive P0 (chỉ 5)

1. `Field` / `Input` / `Select` / `TextArea` — thay 5 bản copy `fieldClass`.
2. `Panel` / `Section` / `Disclosure`.
3. `Toast` + `useNotices` (tone, queue, aria-live).
4. `ConfirmDialog` + `Drawer` (focus trap, `Esc`, trả focus, `aria-modal`).
5. `Segmented` / `Tabs` (`role=tablist`, `aria-selected`, điều hướng phím).

Các primitive khác (`StatusPill`, `EmptyState`, `CommandPalette`…) làm khi cần, không gom vào P0.

### 7.2. Tách `AiEffects` theo strangler

Không đổi behavior trong từng bước; giữ props/API cũ, ship dần:

1. `useStudioAssets()` — một fetch, summary; thay 2 nơi gọi hiện tại.
2. `ClipPanel` — render nhanh/chuẩn, trạng thái, lưu; dùng ở Studio action bar.
3. `ObjectInspector` — list + vẽ tay + xoá.
4. `EffectParams` + `PromptBar`.
5. `StageCanvas` (giữ `PlanOverlay`).
6. `StudioEditor` composition; adapter cho entry point Karaoke; cập nhật
   `docs/living-still-interfaces.md`.

Mục tiêu: `studio-workspace.tsx` < 250 dòng, `ai-effects.tsx` < 400 dòng sau khi xong.

### 7.3. Backend/API changes

| API | Thay đổi | Ưu tiên |
|---|---|---|
| `GET /api/studio/assets` | `?summary=1`: bỏ `effect_plan`, thêm `duration_seconds`, `used` (nếu bật); detail khi open | P0 |
| `GET /api/studio/assets/{id}/thumb` | FFmpeg extract 1 frame, cache, version, single-flight | P0 |
| `PATCH /api/studio/assets/{id}` | rename; tuỳ chọn `asset_id` upsert | P0 (rename) / P1 (upsert) |
| `POST /api/studio/assets/{id}/delete` hoặc `DELETE` | guard reference; soft-delete/trash | P0 |
| `POST /api/studio/assets/cleanup` | `dry_run=1` trả `{count, bytes, ids}` | P0 |
| `GET /api/projects/recent` | st_mtime, dedupe, cap 12, cache | P1 |
| `GET/PUT /api/settings` | consent, device, output defaults; precedence `project > settings > default` | P2 |
| `POST /api/studio/render-job/{id}/cancel` | chỉ thêm nếu thực sự muốn nút Huỷ | P2 |
| `/api/studio/assets/{id}/open` | đổi nghĩa thành `edit`; thêm `reveal` riêng | P1 (alias để back-compat) |

Ghi chú payload: meta đã có `clip_size`, `asset_frames`, `fps`; **không** thêm field trùng.
`duration_seconds` chỉ là giá trị tính sẵn cho UI.

`used`:

- `_referenced_asset_ids` hiện trả `set` hoặc `None` và quét mọi project mỗi lần gọi.
- Cần `asset_id → [project_path]`, cache theo `(path, mtime, size)`; nếu scan lỗi → UI hiện
  `không xác định`, không hiện `chưa dùng`.

Asset safety:

- `save_asset_from_preview` tạo `bg_<uuid>` mới mỗi lần; `asset_fingerprint` chỉ dùng cho
  segment cache. Trước khi auto-save phải có:
  - upsert theo `(image_hash, plan_hash, profile fingerprint, frames)` hoặc `asset_id` đang sửa;
  - eviction không bỏ qua asset mới (<30 ngày) một cách mù quáng — cần quota + LRU có ý thức;
  - reference guard cho delete;
  - thông báo khi asset bị xoá trong lúc đang mở.

---

## 8. Roadmap & DoD

Ước lượng theo người đã quen codebase; cột `Dep` là phụ thuộc.

| # | Việc | Issues | Công | Dep | DoD |
|---|---|---|---|---|---|
| 0 | Quick wins: naming + active nav; bỏ card “Luồng”; toast có tone (success không đỏ); lazy asset fetch; inline rename; asset card aspect/preload; bỏ nút Huỷ giả | U5, U12, U14, U15 | 0,5–1 ngày | — | tsc pass; không còn success nền đỏ; không `window.prompt` |
| 1a | Dirty registry + `guardedNavigate` + `ConfirmDialog` | U1, U10 | 0,5–1 ngày | 0 | rời trang dirty có dialog; Back/reload có draft |
| 1b | Lift editor draft (`lines/removed/dirty/selectedLine/exportDir`) + autosave sessionStorage | U1, U10 | 1–1,5 ngày | 1a | sửa timing, đổi tab/route, quay lại còn draft |
| 2 | Asset safety: reference guard, `used` map cache, cleanup dry-run, thumbnail, rename | U7, U20, U6 | 1–1,5 ngày | — | pytest cho guard/dry-run/thumb; UI xoá chặn asset đang dùng |
| 3 | `AiEffects` strangler, tối thiểu `useStudioAssets` + `ClipPanel`; phần còn lại sau | U6, U11 | 1,5–3 ngày | — | 1 lần fetch asset; Studio dùng ClipPanel; không đổi behavior |
| 4 | Studio: 2 tab keep-mounted, 3 vùng, inspector accordion, object list compact, thư viện toolbar/grid/list | U2, U3, U4, U14 | 3–4 ngày | 3 | 1280×800 không scroll editor; ≥4 card/hàng; list view |
| 5 | Karaoke: setup compact + effects drawer dùng chung + export drawer + compact line rows | U8, U9, U17 | 2–3 ngày | 3 | setup giảm ≥40% với fixture; drawer keep-mounted |
| 6 | Job store + global status + server health banner | U16, U18 | 1 ngày | 1a | polling tiếp tục khi đổi route; 1 status surface |
| 7 | Upsert/fingerprint + auto-save (chỉ sau 2 & 3) | U7, U11 | 1–1,5 ngày | 2, 3 | 5 lần lưu cùng plan → 1 asset; quota rõ |

**P0 thực tế: ~10–15 ngày**, không phải 5–7. Critical path là **#3 (`AiEffects`)**; có thể rút
#3 xuống 1,5 ngày nếu chỉ tách `useStudioAssets` + `ClipPanel`, phần object/params làm sau.

**P1:**

| Việc | Công |
|---|---|
| Persistence đầy đủ (provider + autosave project/draft + rehydrate precedence) | 2–3 ngày |
| `GET /api/projects/recent` + dashboard | 1–1,5 ngày |
| Effects drawer dùng chung trong Setup (nếu chưa xong ở P0 #5) | 1–1,5 ngày |
| Settings drawer + API | 1–1,5 ngày |
| Left rail **chỉ khi** ≥3 destination + persistence xong | 1–2 ngày |
| A11y pass cho dialog/tab/canvas note | 0,5–1 ngày |

**P2:** bulk asset actions, command palette, soft-delete/trash UI, asset references chi tiết,
  upsert nâng cao, cache thumbnail dài hạn.

Thứ tự khuyến nghị: **0 → 1a → 2 → 3 → 1b → 4 → 5 → 6 → 7 → P1**.

---

## 9. Test plan & acceptance

### 9.1. Test tự động

- `cd web && npx tsc --noEmit` (điều kiện cần, không phải đủ).
- pytest:
  - `test_assets`: delete guard chặn asset đang được project tham chiếu; cleanup `dry_run`
    không xoá; thumbnail trả file + cache; `?summary=1` không chứa `effect_plan`.
  - `test_projects_recent`: dedupe, bỏ file không tồn tại, sort theo `st_mtime`, cap 12.
  - `test_studio`: preview nhanh **không** lưu được asset; full-res lưu được; asset 16s
    không valid với project 8s.
  - `test_settings` (P2): precedence `project > settings > default`; consent receipt vẫn ghi.
- Playwright/manual smoke (chưa có framework, ghi checklist):
  - Studio → analyze → apply → quick → full → save → library → open.
  - Karaoke setup → align → edit → export; mở drawer hiệu ứng 2 entry point.

### 9.2. Acceptance

**Studio**

- 1280×800, tab Biên tập không scroll dọc ở trạng thái mặc định (fixture 3 ảnh).
- Stage ≥520px khi không rail; inspector chỉ mở 1 section.
- Object list 1 cột compact; affordance chỉ hiện khi selected/hover.
- Thư viện: ≥4 card/hàng ở 1280; search accent-insensitive; có list view; card 9:16/1:1
  không bị crop.
- `Xem nhanh` không thể lưu; `Tạo clip chuẩn` lưu được; không `window.prompt`.
- Mở asset khi có draft → dialog, không thay thế im lặng.
- Không còn nhiều hơn 1 action toast + 1 job status cùng lúc.

**Karaoke**

- Setup với fixture: chiều cao giảm ≥40% sau khi hiệu ứng vào drawer; các section tuỳ chọn
  đóng mặc định.
- Hiệu ứng drawer đóng/mở không mất `prompt/analysis/objects`.
- Biên tập: câu compact; chỉ câu chọn mở editor chữ; export drawer không chiếm chỗ thường trực.
- Sửa timing rồi đổi route/quay lại: draft còn; dialog chọn bản nháp nếu khác bản lưu.

**Navigation**

- `Dự án ↔ AI Studio` không mất job/draft.
- `/studio?tab=library` refresh đúng tab; `?asset=` mở đúng asset và Back về thư viện.
- `/project?project=…` refresh mở lại project; nếu `?job=` 404, fallback project + thông báo.
- Server offline: 1 banner toàn cục + `Thử lại`, không lặp lỗi ở từng action.

**Hiệu năng**

- 50 asset: mở thư viện không tải 50 video; chỉ ảnh poster; hover mới play.
- Polling không re-render toàn bộ Studio mỗi giây (kiểm bằng React Profiler hoặc log render).
- `GET /api/studio/assets?summary=1` với 50 asset < 100KB.

### 9.3. A11y checklist (P0)

- Drawer/dialog: focus trap, `Esc`, trả focus, `aria-modal`, label.
- Tabs: `role=tablist`, `aria-selected`, phím ←/→.
- Nav: `aria-current="page"`.
- Toast: `aria-live` đúng tone.
- Canvas vẽ object: ghi rõ “thao tác vẽ cần chuột”; các checkbox object vẫn dùng bàn phím được.

---

## 10. Rủi ro

| Rủi ro | Xử lý |
|---|---|
| Mất timing đang sửa | #1a + #1b trước khi đổi layout; autosave sessionStorage; dialog khôi phục |
| Back của trình duyệt vẫn làm mất draft | Autosave + tài liệu giới hạn; không hứa guard tuyệt đối |
| Auto-save tạo bản trùng, đầy quota | Chỉ bật sau upsert + eviction + guard; mặc định opt-in |
| Xoá asset đang dùng → export fallback âm thầm | Guard reference + hiện “đang dùng trong N dự án”; soft-delete |
| `list_assets` payload lớn | `?summary=1`; detail khi mở; đo bằng fixture 50 asset |
| `used` quét project mỗi lần list | Cache theo mtime; trả `unknown` khi scan lỗi |
| Thumbnail FFmpeg nặng | Cache + version + single-flight; fallback poster |
| Tách `AiEffects` gây regression | Strangler, giữ props; smoke cả 2 entry point; test payload |
| Drawer unmount mất state | Keep-mounted hoặc lift state; test đóng/mở |
| Polling làm re-render cả cây | Selector/`useSyncExternalStore`, không context toàn object |
| Rail 240px làm stage quá nhỏ | D1: chưa làm rail; nếu làm, icon 72px |
| Mobile là đầu tư sai | D11: non-goal; test cửa sổ hẹp 1024–1280 |
| Settings precedence gây khó hiểu | Ghi rõ `project > settings > default`; UI hiện nguồn |
| Naming/route đổi làm vỡ bookmark cũ | Giữ `/studio`; thêm redirect/alias cho `/` cũ nếu cần |
| Trích dẫn dòng lỗi thời | D12: dùng symbol/component |

---

## 11. Quan hệ với doc cũ

`docs/background-performance-ux-proposal.md` vẫn có hiệu lực. Spec này **kế thừa** các điểm:

- Drawer hiệu ứng dùng chung, một CTA, auto-save, giảm phase.
- **Điều kiện an toàn giữ nguyên**:
  - auto-save chỉ sau quota/GC;
  - không tự đổi `image_duration` toàn cục theo asset;
  - giữ `effect_consent_receipt`;
  - render bản chuẩn theo `image_duration` của dự án;
  - single-flight theo cache key (khi làm auto-save).
- Spec này **thay đổi** so với doc cũ:
  - stepper 3 → 2 bước + export status (D3);
  - đề xuất left nav bị hoãn (D1);
  - thêm upsert/eviction/guard trước auto-save (D5/D6);
  - settings thành drawer, chưa thành route (D9).

---

## 12. Phụ lục

### 12.1. Wireframe shell tương lai (chỉ khi D1 đủ điều kiện)

```text
┌──────┬───────────────────────────────────────────────────────────────────────┐
│  ◆   │  Dự án / Tên bài                        [Lưu] [Xuất ▸]               │
│  ▣   ├───────────────────────────────────────────────────────────────────────┤
│  ✦   │                                                                       │
│  ▤   │  nội dung                                                             │
│  ⚙   │                                                                       │
└──────┴───────────────────────────────────────────────────────────────────────┘
```

Icon rail 72px, tooltip, badge job; **không** dùng 240px ở 1280.

### 12.2. Glossary

- **Clip**: asset video đã render, lưu trong thư viện.
- **Preview nhanh**: bản 640px, không lưu được.
- **Bản chuẩn**: full-res theo profile, lưu được.
- **Draft**: state đang sửa, chưa ghi vào project file.
- **Durable**: còn sau khi tắt app (project file, asset).
- **Transient**: chỉ trong phiên (job, draft).

### 12.3. Việc cần đo trước khi code

1. Điền các `TBD` ở §2.1 bằng fixture thật.
2. Xác nhận `?summary=1` tiết kiệm bao nhiêu với 50 asset hiện có.
3. Đo thời gian `Xem nhanh` vs `Tạo clip chuẩn` trên máy đích để copy chính xác.
4. Kiểm tra `_referenced_asset_ids` mất bao lâu với số project thực tế.


---

## 13. Kết quả triển khai (2026-11-22)

Đã làm trong PR `feat/ux-navigation-studio-polish`:

### Navigation & state

- Top bar + segmented `Dự án | AI Studio` (D1: **chưa làm left rail 240px**), active state,
  badge job đang chạy, badge `Chưa lưu`, nút Cài đặt.
- Settings drawer (consent Gemini, device, model, thư mục xuất mặc định) + API `GET/PUT /api/settings`.
- `sessionStorage` cho phase/fields/jobId/studio draft/timing draft; refresh và đổi route
  trong cùng tab không mất việc.
- Dirty registry + `beforeunload` + `ConfirmDialog` khi rời trang; `saveAllDirty` gọi lưu timing.
- Global job store dùng `useSyncExternalStore`; polling tiếp tục khi đổi route; banner
  server offline + `Thử lại`.

### Karaoke

- Dashboard `/` với dự án gần đây (`GET /api/projects/recent`, `st_mtime`).
- Workspace `/project?project=` (canonical), `?new=1` cho dự án mới; Back về dashboard.
- Setup: bỏ card “Luồng”, checklist bên phải, `Hiệu ứng nền`/`Tuỳ chọn xuất`/`Nâng cao`
  thành disclosure, hiệu ứng vào Drawer dùng chung `AiEffects`.
- Editor: danh sách câu compact, chỉ câu chọn mở editor chữ; export + log + nền vào Drawer;
  nút `Xuất` + status chip trên timeline.
- `notice` thành công dùng toast; lỗi field/job vẫn inline.

### AI Studio

- 2 tab keep-mounted: Biên tập / Thư viện clip.
- Biên tập 3 vùng: Ảnh nguồn → Sân khấu → Thuộc tính; object list compact 1 cột;
  effect params gọn; `Xem nhanh` 640px và `Tạo clip chuẩn` tách nghĩa; preview clip ngay trên sân khấu.
- Thư viện: search accent-insensitive, lọc profile, sort, grid/list, thumbnail JPEG, hover video,
  đổi tên, tải, mở thư mục, xoá (guard reference), cleanup dry-run.
- Asset card dùng aspect theo profile + `object-contain` (hết crop 9:16/1:1).

### Backend

- `GET /api/studio/assets?summary=1&usage=1`, `GET /{id}/thumb`, `PATCH /{id}` (rename),
  `DELETE /{id}` (guard + force), `POST /cleanup?dry_run=1`, `POST /{id}/edit`, `POST /{id}/reveal`.
- `GET /api/projects/recent`, `GET/PUT /api/settings`, `POST /api/studio/render-job/{id}/cancel`.
- `referenced_asset_projects()` có cache theo mtime/size; `used_projects` gồm project file + job đang mở.
- Auto-save dùng lại asset cùng `image_hash + plan_hash + profile + frames` thay vì tạo bản trùng.

### Chưa làm / khác spec

- **Chưa tách `AiEffects` thành các file nhỏ** như §7.2; mới tách `ImageTray`, `AssetLibrary`,
  primitives và hook dùng chung ở mức đủ dùng. `ai-effects.tsx` vẫn còn lớn.
- Auto-save chưa có upsert theo `asset_id`/PATCH; mới dedupe theo fingerprint khi tạo mới.
  Chưa bật auto-save cho mọi trường hợp ngoài hành vi cũ của `AiEffects`.
- Chưa có soft-delete/trash; xoá cứng sau khi guard reference.
- Chưa persist `analysis/objects/prompt` của Studio qua route change; chỉ persist ảnh,
  effects, profile, duration. Trong cùng tab Biên tập/Thư viện thì state được giữ vì keep-mounted.
- Back của trình duyệt không chặn được đầy đủ; đã bù bằng session draft + `beforeunload`.
- Mobile phone vẫn là non-goal; đã test build và layout ở breakpoint desktop.

### Kiểm tra

- `npx tsc --noEmit`: pass.
- `npm run build`: pass (4 route: `/`, `/project`, `/studio`, `/_not-found`).
- `python -m unittest discover -s tests`: **202 tests OK**.
- API smoke qua server thật: `/api/health`, `/api/settings`, `/api/projects/recent`,
  `/api/studio/assets?summary=1&usage=1`, `/api/studio/assets/usage` đều 200.
- UI smoke bị giới hạn vì môi trường không có browser automation; đã kiểm tra SSR 200 cho
  `/`, `/studio`, `/project`, build production, và review thủ công các luồng chính.
