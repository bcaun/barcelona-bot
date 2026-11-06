import { tg } from "./tg";

export interface Question {
  number: number;
  total: number;
  id: string;
  topic: string;
  text: string;
  options: string[];
}

export interface Me {
  lang: "ru" | "en";
  name: string;
  total: number;
  lives: number;
  attempts_left: number;
  active_session: string | null;
  best_score: number | null;
  games_played: number;
  won_ever: boolean;
  prize_claimed: boolean;
  claim_url: string | null;
  subscribed: boolean;
  channel_url: string;
  share_url: string;
  prize_requires_subscription: boolean;
}

export interface StartResp {
  session_id: string;
  total: number;
  lives: number;
  score: number;
  question: Question | null;
}

export interface GameResult {
  score: number;
  total: number;
  won: boolean;
  better_than_pct: number;
  in_leaderboard: boolean;
  claim_url: string | null;
  share_url: string;
}

export interface AnswerResp {
  correct: boolean;
  correct_index: number;
  explain: string;
  tool: "calc" | "check" | null;
  lives: number;
  score: number;
  finished: boolean;
  next: Question | null;
  result?: GameResult;
}

export interface Leaderboard {
  top: { rank: number; name: string; score: number; won: boolean; me: boolean }[];
  my_rank: number | null;
  players: number;
  total: number;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`./api${path}`, {
    method,
    headers: {
      Authorization: `tma ${tg?.initData ?? ""}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.json()).detail ?? "";
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  me: () => call<Me>("GET", "/me"),
  start: () => call<StartResp>("POST", "/quiz/start"),
  answer: (sid: string, position: number, choice: number) =>
    call<AnswerResp>("POST", `/quiz/${sid}/answer`, { position, choice }),
  leaderboard: () => call<Leaderboard>("GET", "/leaderboard"),
};
