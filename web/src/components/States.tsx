export function Loading() {
  return <div className="loading">載入中…</div>;
}

export function ErrorState({ error }: { error: Error }) {
  return <div className="error">資料載入失敗：{error.message}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
