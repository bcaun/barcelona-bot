// Minimal typed wrapper around Telegram.WebApp (https://core.telegram.org/bots/webapps).

interface BottomButton {
  setText(text: string): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface WebApp {
  initData: string;
  initDataUnsafe: { user?: { language_code?: string; first_name?: string } };
  colorScheme: "light" | "dark";
  MainButton: BottomButton;
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
  HapticFeedback?: {
    notificationOccurred(type: "error" | "success" | "warning"): void;
    impactOccurred(style: "light" | "medium" | "heavy"): void;
  };
  ready(): void;
  expand(): void;
  close(): void;
  openTelegramLink(url: string): void;
  openLink(url: string): void;
  enableClosingConfirmation(): void;
  disableClosingConfirmation(): void;
}

declare global {
  interface Window {
    Telegram?: { WebApp: WebApp };
  }
}

export const tg: WebApp | undefined = window.Telegram?.WebApp;

let mainHandler: (() => void) | null = null;

/** Show the native MainButton with a single click handler (replaces the previous one). */
export function mainButton(text: string | null, onClick?: () => void): void {
  if (!tg) return;
  if (mainHandler) tg.MainButton.offClick(mainHandler);
  mainHandler = null;
  if (text === null) {
    tg.MainButton.hide();
    return;
  }
  tg.MainButton.setText(text);
  if (onClick) {
    mainHandler = onClick;
    tg.MainButton.onClick(onClick);
  }
  tg.MainButton.enable();
  tg.MainButton.show();
}

let backHandler: (() => void) | null = null;

export function backButton(onClick: (() => void) | null): void {
  if (!tg) return;
  if (backHandler) tg.BackButton.offClick(backHandler);
  backHandler = onClick;
  if (onClick) {
    tg.BackButton.onClick(onClick);
    tg.BackButton.show();
  } else {
    tg.BackButton.hide();
  }
}

export function haptic(kind: "success" | "error"): void {
  tg?.HapticFeedback?.notificationOccurred(kind);
}

export function openTelegram(url: string): void {
  if (tg) tg.openTelegramLink(url);
  else window.open(url, "_blank");
}
