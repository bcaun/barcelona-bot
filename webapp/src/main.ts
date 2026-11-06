import "./style.css";
import { api, ApiError, type AnswerResp, type GameResult, type Me, type Question } from "./api";
import { t, setLang, type Key } from "./i18n";
import { backButton, haptic, mainButton, openTelegram, tg } from "./tg";

const root = document.getElementById("app")!;

interface Game {
  sid: string;
  lives: number;
  score: number;
  question: Question | null;
}

let me: Me | null = null;
let game: Game | null = null;

// ---------------------------------------------------------------- tiny DOM helpers

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: { class?: string; text?: string; html?: string } = {},
  children: (Node | null)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (attrs.class) node.className = attrs.class;
  if (attrs.text !== undefined) node.textContent = attrs.text; // server data: always as text
  if (attrs.html !== undefined) node.innerHTML = attrs.html; // only our own i18n strings
  for (const c of children) if (c) node.append(c);
  return node;
}

function button(label: string, onClick: () => void, cls = "btn"): HTMLButtonElement {
  const b = el("button", { class: cls, text: label });
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function screen(...children: (Node | null)[]): void {
  root.replaceChildren(...children.filter((c): c is Node => c !== null));
  window.scrollTo(0, 0);
}

function hearts(lives: number, total: number): HTMLElement {
  const h = el("div", { class: "hearts", text: "❤️".repeat(lives) + "🤍".repeat(Math.max(0, total - lives)) });
  h.setAttribute("aria-label", t("hearts_label", { n: lives }));
  return h;
}

function toast(key: Key): void {
  const n = el("div", { class: "toast", text: t(key) });
  document.body.append(n);
  setTimeout(() => n.remove(), 3000);
}

function botLink(start: string): string | null {
  // share_url is https://t.me/<bot>/<app>?startapp=…  ->  https://t.me/<bot>?start=<start>
  const m = me?.share_url.match(/^https:\/\/t\.me\/([^/]+)\//);
  return m ? `https://t.me/${m[1]}?start=${start}` : null;
}

async function guarded<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.detail === "already_played_today") {
      await showStart();
      return null;
    }
    toast("error");
    return null;
  }
}

// ---------------------------------------------------------------- screens

async function showStart(): Promise<void> {
  backButton(null);
  tg?.disableClosingConfirmation();
  const data = await guarded(() => api.me());
  if (!data) return;
  me = data;
  setLang(me.lang);
  const total = me.total;
  const status: (Node | null)[] = [];
  if (me.best_score !== null) status.push(el("p", { class: "muted", text: t("best", { score: me.best_score, total }) }));
  if (me.claim_url) {
    status.push(el("div", { class: "card prize" }, [
      el("p", { html: t("won_text") }),
      button(t("claim"), () => openTelegram(me!.claim_url!), "btn primary"),
    ]));
  } else if (me.prize_claimed) {
    status.push(el("p", { class: "muted", text: t("claimed") }));
  }
  const canPlay = me.active_session !== null || me.attempts_left > 0;
  if (!canPlay) status.push(el("p", { class: "note", text: t("no_attempts") }));

  screen(
    el("header", { class: "hero" }, [
      el("div", { class: "logo", text: "🏠" }),
      el("h1", { text: t("title") }),
    ]),
    el("p", { text: t("intro", { total, lives: me.lives }) }),
    el("p", { class: "card", html: t("prize") }),
    el("p", { class: "muted", text: t("rules_attempts") }),
    ...status,
    el("div", { class: "row" }, [
      button(t("leaderboard"), () => void showLeaderboard()),
      button(t("channel"), () => openTelegram(me!.channel_url)),
    ]),
  );
  if (canPlay) mainButton(t(me.active_session ? "resume" : "start"), () => void startGame());
  else mainButton(null);
}

async function startGame(): Promise<void> {
  tg?.MainButton.showProgress();
  const s = await guarded(() => api.start());
  tg?.MainButton.hideProgress();
  if (!s) return;
  game = { sid: s.session_id, lives: s.lives, score: s.score, question: s.question };
  tg?.enableClosingConfirmation();
  showQuestion();
}

function showQuestion(): void {
  if (!game?.question || !me) return;
  const q = game.question;
  backButton(null);
  mainButton(null);
  let locked = false;
  const options = q.options.map((text, i) =>
    button(text, () => {
      if (locked) return;
      locked = true;
      void answer(i, options);
    }, "option"),
  );
  screen(
    el("div", { class: "topbar" }, [
      el("span", { class: "muted", text: t("question", { n: q.number, total: q.total }) }),
      hearts(game.lives, me.lives),
    ]),
    el("div", { class: "progress" }, [progressBar(q.number - 1, q.total)]),
    el("h2", { class: "question", text: q.text }),
    el("div", { class: "options" }, options),
  );
}

function progressBar(done: number, total: number): HTMLElement {
  const bar = el("div", { class: "progress-fill" });
  bar.style.width = `${Math.round((100 * done) / total)}%`;
  return bar;
}

async function answer(choice: number, buttons: HTMLButtonElement[]): Promise<void> {
  if (!game?.question) return;
  buttons.forEach((b) => (b.disabled = true));
  buttons[choice].classList.add("chosen");
  const r = await guarded(() => api.answer(game!.sid, game!.question!.number - 1, choice));
  if (!r) {
    buttons.forEach((b) => (b.disabled = false));
    buttons[choice].classList.remove("chosen");
    showQuestion();
    return;
  }
  haptic(r.correct ? "success" : "error");
  buttons[r.correct_index].classList.add("right");
  if (!r.correct) buttons[choice].classList.add("wrong");
  game.lives = r.lives;
  game.score = r.score;
  game.question = r.next;
  root.append(feedback(r));
  root.querySelector(".hearts")?.replaceWith(hearts(r.lives, me!.lives));
  root.querySelector(".feedback")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (r.finished && r.result) {
    const result = r.result;
    mainButton(t("finish"), () => showResult(result));
  } else {
    mainButton(t("next"), () => showQuestion());
  }
}

function feedback(r: AnswerResp): HTMLElement {
  const tool = r.tool ? botLink(r.tool) : null;
  return el("div", { class: `feedback ${r.correct ? "ok" : "bad"}` }, [
    el("strong", { text: t(r.correct ? "correct" : "wrong") }),
    el("p", { text: r.explain }),
    tool ? button(t(r.tool === "calc" ? "tool_calc" : "tool_check"), () => openTelegram(tool), "link") : null,
  ]);
}

function showResult(res: GameResult): void {
  tg?.disableClosingConfirmation();
  backButton(null);
  const shareText = res.won ? t("share_text_won") : t("share_text", { score: res.score, total: res.total });
  const share = () =>
    openTelegram(`https://t.me/share/url?url=${encodeURIComponent(res.share_url)}&text=${encodeURIComponent(shareText)}`);
  const parts: (Node | null)[] = [
    el("header", { class: "hero" }, [
      el("div", { class: "logo", text: res.won ? "🏆" : "🏁" }),
      el("h1", { text: res.won ? t("won_title", { total: res.total }) : t("lost_title") }),
    ]),
    el("p", { class: "big", text: t("score", { score: res.score, total: res.total }) }),
    res.better_than_pct > 0 ? el("p", { class: "muted", text: t("better", { pct: res.better_than_pct }) }) : null,
  ];
  if (res.won) {
    parts.push(el("div", { class: "card prize" }, [
      el("p", { html: t("won_text") }),
      me?.prize_requires_subscription && !me.subscribed ? el("p", { class: "muted", text: t("sub_needed") }) : null,
      res.claim_url ? button(t("claim"), () => openTelegram(res.claim_url!), "btn primary") : el("p", { text: t("claimed") }),
    ]));
  } else {
    parts.push(el("p", { text: t("lost_text") }));
  }
  if (!res.in_leaderboard) parts.push(el("p", { class: "note", text: t("not_ranked") }));
  parts.push(el("div", { class: "row" }, [
    button(t("leaderboard"), () => void showLeaderboard()),
    button(t("channel"), () => openTelegram(me!.channel_url)),
  ]));
  screen(...parts);
  mainButton(t("share"), share);
}

async function showLeaderboard(): Promise<void> {
  const lb = await guarded(() => api.leaderboard());
  if (!lb) return;
  mainButton(null);
  backButton(() => void showStart());
  const rows = lb.top.map((r) =>
    el("li", { class: r.me ? "me" : "" }, [
      el("span", { class: "rank", text: r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : String(r.rank) }),
      el("span", { class: "name", text: r.me ? `${r.name} (${t("lb_you")})` : r.name }),
      el("span", { class: "pts", text: `${r.score}/${lb.total}` }),
    ]),
  );
  screen(
    el("h1", { text: t("lb_title") }),
    lb.my_rank ? el("p", { class: "muted", text: t("lb_me", { rank: lb.my_rank, players: lb.players }) }) : null,
    rows.length ? el("ol", { class: "board" }, rows) : el("p", { class: "muted", text: t("lb_empty") }),
    me && !me.subscribed ? el("p", { class: "note", text: t("not_ranked") }) : null,
    button(t("back"), () => void showStart()),
  );
}

// ---------------------------------------------------------------- boot

setLang(tg?.initDataUnsafe.user?.language_code);
if (!tg || !tg.initData) {
  screen(el("header", { class: "hero" }, [el("div", { class: "logo", text: "🏠" }), el("h1", { text: t("title") })]),
    el("p", { text: t("only_telegram") }));
} else {
  tg.ready();
  tg.expand();
  void showStart();
}
