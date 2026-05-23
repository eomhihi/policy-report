import { sql } from "@vercel/postgres";

type SearchResult = {
  inst_code: string;
  doc_title: string;
  pub_year: number;
  section_title: string;
  body_text: string;
};

type PageProps = {
  searchParams: Promise<{ q?: string }>;
};

async function searchDocuments(keyword: string): Promise<SearchResult[]> {
  const pattern = `%${keyword}%`;

  const { rows } = await sql<SearchResult>`
    SELECT
      m.inst_code,
      m.doc_title,
      m.pub_year,
      c.section_title,
      c.body_text
    FROM document_contents c
    INNER JOIN document_master m ON c.doc_id = m.doc_id
    WHERE c.body_text ILIKE ${pattern}
       OR c.section_title ILIKE ${pattern}
    ORDER BY m.pub_year DESC, m.doc_title ASC, c.section_title ASC
  `;

  return rows;
}

export default async function HomePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const query = params.q?.trim() || "자율주행";

  let results: SearchResult[] = [];
  let errorMessage: string | null = null;

  try {
    results = await searchDocuments(query);
  } catch (error) {
    errorMessage =
      error instanceof Error
        ? error.message
        : "데이터베이스 연결에 실패했습니다.";
  }

  return (
    <div className="min-h-screen bg-slate-50 text-[#124559]">
      <header className="border-b border-[#aec3b0] bg-white">
        <div className="mx-auto max-w-5xl px-6 py-10">
          <p className="text-sm font-medium tracking-[0.2em] text-[#598392] uppercase">
            Sejong Future Industry Intelligence
          </p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-[#124559]">
            세종시 미래산업 보고서 지식 허브
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-[#598392]">
            파싱된 정책 보고서 조각을 주제별로 검색하고, 출처와 섹션 맥락을
            함께 확인합니다.
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-10">
        <section className="rounded-none border border-[#aec3b0] bg-white p-6">
          <form action="/" method="GET" className="flex flex-col gap-4 sm:flex-row">
            <label htmlFor="search-query" className="sr-only">
              검색어
            </label>
            <input
              id="search-query"
              name="q"
              type="search"
              defaultValue={query}
              placeholder="검색어를 입력하세요"
              className="min-w-0 flex-1 rounded-none border border-[#aec3b0] bg-white px-4 py-3 text-[#124559] outline-none placeholder:text-[#598392]/70 focus:border-[#598392]"
            />
            <button
              type="submit"
              className="rounded-none border border-[#598392] bg-[#598392] px-6 py-3 text-sm font-medium tracking-wide text-white transition-colors hover:border-[#124559] hover:bg-[#124559]"
            >
              검색
            </button>
          </form>
          <p className="mt-4 text-sm text-[#598392]">
            현재 검색어:{" "}
            <span className="font-medium text-[#124559]">&quot;{query}&quot;</span>
            {results.length > 0 && (
              <span className="ml-2">· {results.length}건</span>
            )}
          </p>
        </section>

        {errorMessage ? (
          <section className="mt-8 rounded-none border border-[#aec3b0] bg-white p-6">
            <h2 className="text-lg font-semibold text-[#124559]">
              데이터를 불러오지 못했습니다
            </h2>
            <p className="mt-3 text-sm leading-6 text-[#598392]">
              Vercel Postgres 연결 정보를 확인해 주세요.{" "}
              <code className="bg-gray-50 px-1 py-0.5 text-[#124559]">
                web/.env.local
              </code>{" "}
              파일의{" "}
              <code className="bg-gray-50 px-1 py-0.5 text-[#124559]">
                POSTGRES_URL
              </code>{" "}
              값을 설정한 뒤 개발 서버를 재시작하세요.
            </p>
            <p className="mt-3 text-xs text-[#598392]/80">{errorMessage}</p>
          </section>
        ) : results.length === 0 ? (
          <section className="mt-8 rounded-none border border-[#aec3b0] bg-white p-10 text-center">
            <h2 className="text-lg font-semibold text-[#124559]">
              검색 결과가 없습니다
            </h2>
            <p className="mt-3 text-sm leading-6 text-[#598392]">
              다른 키워드로 다시 검색해 보세요.
            </p>
          </section>
        ) : (
          <section className="mt-8 space-y-6">
            {results.map((result, index) => (
              <article
                key={`${result.doc_title}-${result.section_title}-${index}`}
                className="rounded-none border border-[#aec3b0] bg-white"
              >
                <div className="border-b border-[#aec3b0] px-6 py-5">
                  <div className="flex flex-wrap items-center gap-3 text-xs font-medium tracking-wide text-[#598392] uppercase">
                    <span className="rounded-none border border-[#aec3b0] px-2 py-1">
                      {result.inst_code}
                    </span>
                    <span>{result.pub_year}</span>
                  </div>
                  <h2 className="mt-3 text-xl font-semibold leading-8 text-[#124559]">
                    {result.section_title}
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-[#598392]">
                    {result.doc_title}
                  </p>
                </div>

                <div className="bg-gray-50 px-6 py-5">
                  <p className="whitespace-pre-wrap text-sm leading-7 text-[#124559]">
                    {result.body_text}
                  </p>
                </div>
              </article>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
