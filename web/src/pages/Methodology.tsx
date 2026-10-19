import { useMeta, usePageTitle } from "../data";
import { PAGES } from "../site";
import { ErrorState, Loading } from "../components/States";

export default function Methodology() {
  const meta = useMeta();
  usePageTitle(PAGES.methodology.title);
  if (meta.error) return <ErrorState error={meta.error} />;
  if (!meta.data) return <Loading />;
  const m = meta.data;
  const p = m.params.polarization;
  const e = m.params.events;
  const model = (k: string) => (m.models[k] ? `${m.models[k].model}${m.models[k].reasoning_effort ? `（推理強度 ${m.models[k].reasoning_effort}）` : ""}` : "—");

  return (
    <article className="prose">
      <h1>方法論</h1>
      <p>
        第五權以賓州大學計算社會科學實驗室的{" "}
        <a href="https://mediabiasdetector.seas.upenn.edu/methodology/" target="_blank" rel="noopener noreferrer">
          Media Bias Detector
        </a>
        （以下簡稱 MBD）為模版，盡量沿用其方法，並針對台灣的政治環境調整。以下說明每個步驟與 MBD 的異同。
      </p>

      <h2>1. 收集</h2>
      <p>
        追蹤 {m.outlets.length} 家網路媒體：{m.outlets.map((o) => o.short).join("、")}。每小時從各家的 RSS、Google News sitemap 與版面列表頁，
        抓取<strong>政治、選舉、軍事、兩岸與國際版</strong>的全部文章（國際版用來涵蓋涉台外交新聞），再由模型排除與台灣無關的國際新聞。
      </p>
      <p>
        MBD 是每天 5 次抓取首頁、取版面最顯著的 20 篇；第五權改為抓取相關版面的全部文章，因為我們要計算的「報導數」是各家在一個議題上的總報導量，
        而首頁上的政治新聞太少，單一議題的樣本會不足。
      </p>
      <p>
        中時新聞網與民視新聞網的網站會擋自動抓取，我們不嘗試繞過，改由 Yahoo 奇摩新聞的授權轉載取得，因此只包含被轉載的文章。
        各家的社論與評論專區目前不在收集範圍內，版面內的分析與評論文章則會被標註為「新聞分析」或「評論」。
      </p>

      <h2>2. 前處理與來源遮蔽</h2>
      <p>
        和 MBD 一樣，我們移除廣告、樣板文字與媒體的自我指涉，並另外移除記者署名、圖說與「延伸閱讀」等連結。送進模型的文字看不出是哪家媒體，
        模型只能依內容判斷。其他媒體的名稱若是新聞內容的一部分（例如「某電視台主辦辯論會」）則保留。
      </p>

      <h2>3. AI 標註</h2>
      <table>
        <thead>
          <tr>
            <th>項目</th>
            <th>MBD</th>
            <th>第五權</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>範圍、重點摘要、議題、子議題、新聞類型</td>
            <td>GPT-4o-mini／GPT-4o，議題與子議題分兩段判斷</td>
            <td>{model("stage1")}，一次輸出，依序判斷</td>
          </tr>
          <tr>
            <td>語氣、政黨傾向（文章與標題各一）</td>
            <td>GPT-4o，−5～+5，先寫理由再給分數</td>
            <td>{model("stage2")}，同樣先寫理由再給分數</td>
          </tr>
          <tr>
            <td>對中立場</td>
            <td>無</td>
            <td>新增，與上一列同時評分</td>
          </tr>
          <tr>
            <td>事件分群用的嵌入</td>
            <td>OpenAI 嵌入模型</td>
            <td>{model("embed")}</td>
          </tr>
          <tr>
            <td>事件標題、子議題整併</td>
            <td>GPT-4o-mini</td>
            <td>{model("synth")}</td>
          </tr>
        </tbody>
      </table>

      <h3>綠藍傾向</h3>
      <p>
        沿用 MBD 的定義：「這則新聞是支持民進黨還是國民黨？支持一個政黨，可以是支持它的觀點、政治人物或政策。」
        −5 表示強烈支持民進黨，+5 表示強烈支持國民黨，0 表示沒有明顯傾向。判斷依據是文本整體對哪一方有利，包括歸責框架、用詞標籤、
        消息來源與篇幅、選材與省略；報導主角是哪一黨並不代表傾向。
      </p>
      <p>
        MBD 只有民主、共和兩黨，第三黨交給模型自行判斷。台灣的民眾黨在立法院是關鍵少數，因此我們在編碼手冊裡寫明：民眾黨依其觀點與哪一方一致來投射，
        與國民黨同陣線批評執政黨時視為偏向國民黨一方，藍白互相攻擊時多半為中立。
      </p>

      <h3>抗中 ↔ 友中</h3>
      <p>
        −5 表示強烈抗中，+5 表示強烈友中。依據包括是否把中國描繪成威脅、主權論述、對兩岸交流的評價、緊張關係歸咎於誰，以及是否未加批判地採用北京官方用語。
        「大陸」「中國」這類習慣稱呼只是很弱的訊號。與中國無關的報導標為「不適用」，不納入平均。
      </p>

      <h3>分組</h3>
      <p>依 MBD，每個軸的分數分成五組：−5～−4、−3～−2、−1～1、2～3、4～5。</p>

      <h2>4. 議題與事件</h2>
      <p>
        議題（{m.topics.length} 個）是固定清單，分屬國內政治、外交、兩岸三類，相當於 MBD 的 topics。子議題是持續一段時間的具體議題，
        除了人工建立的種子清單，系統每天會把被歸為「其他」的報導分組，由模型提議新增或併入既有子議題，並列出變動供人工審核。
      </p>
      <p>
        事件依 MBD 的作法自動分群：在 {e.window_days} 天的滑動視窗內，兩篇報導的嵌入向量 cosine 相似度 ≥ {e.tau} 就連一條邊，
        至少 {e.min_size} 篇相連的報導成為一個事件；過大（&gt; {e.max_size} 篇）或過於鬆散的群組再以 Leiden 演算法切分。
        前後兩天成員重疊達 {e.continuity_jaccard} 以上的事件視為同一事件。
      </p>

      <h2>5. 指標</h2>
      <p>
        每家媒體在每個議題的指標包括：報導數、占該媒體政治新聞的比例、綠藍平均與 95% 信賴區間、抗中友中平均（只計涉中報導）、語氣、新聞類型組成，
        以及轉載中央社稿件的比例。MBD 以平均傾向與報導占比呈現，沒有單一總分；第五權另外為每個議題計算一個極化指數。
      </p>
      <h3>極化指數</h3>
      <p>
        對每一個軸，先找出在這個議題至少有 {p.min_articles_per_outlet} 篇報導的媒體（共 K 家），計算每家的平均位置
        x<sub>o</sub> = 平均分數 ÷ 5，並以經驗貝氏法向議題平均收縮，減少小樣本造成的差距：
      </p>
      <div className="formula">x̃<sub>o</sub> = (n<sub>o</sub>·x<sub>o</sub> + κ·x̄) ⁄ (n<sub>o</sub> + κ)，κ = {p.shrinkage_kappa}</div>
      <p>單軸指數是所有媒體兩兩之間的平均差距（Gini 平均差，相當於 Esteban–Ray 極化指數 α = 0）：</p>
      <div className="formula">P<sub>軸</sub> = 100 × (1 ⁄ K²) × Σ<sub>o</sub> Σ<sub>p</sub> | x̃<sub>o</sub> − x̃<sub>p</sub> |</div>
      <p>
        各家立場一致時為 0；媒體平分兩群、各站一個極端時為 100。最後的極化指數 P 是綠藍軸與抗中友中軸的平均；若涉中報導占比低於 {Math.round(p.china_min_share * 100)}%，
        或有足夠涉中報導的媒體少於 {p.china_min_outlets} 家，就只用綠藍軸。每家媒體權重相同，不以報導量加權，以免發稿量大的媒體或只取得部分轉載的媒體左右結果。
      </p>
      <p>
        信賴區間以媒體內重抽文章的 bootstrap（{p.bootstrap} 次）估計。報導總數少於 {p.min_total_articles} 篇或合格媒體少於 {p.min_outlets} 家時，標示為「樣本不足」。
      </p>
      <h3>選題偏向</h3>
      <p>
        除了立場，MBD 也重視「選擇報導什麼」。我們以各媒體對某議題的關注度（占自家政治新聞的比例）加權各媒體近 90 天的平均綠藍傾向，
        再減去所有媒體的平均：大於 0 表示這個議題主要由偏藍的媒體在報，小於 0 表示主要由偏綠的媒體在報。這個數字不併入極化指數。
      </p>

      <h2>6. 驗證</h2>
      <p>
        AI 標註不是真相。我們依 MBD 的作法建立人工標註的金標準集，標註者看不到媒體名稱與模型分數，並以 Pearson／Spearman 相關、平均絕對誤差、
        五級分組準確率與 Krippendorff's α 比較模型與人的一致性（MBD 報告的綠藍傾向模型與人相關為 0.80，人與人為 0.756）。
        每週另依 MBD 抽樣檢查（每個分組 2 篇、每家媒體至少 1 篇），每月重跑金標準以偵測模型變化。
      </p>

      <h2>7. 限制</h2>
      <ul>
        <li>分數由 AI 依編碼手冊判斷，可能有系統性偏誤；請把它當作經過驗證的估計，而不是定論。</li>
        <li>「支持某黨」的判斷會受議題本身影響，例如某些議題本來就較常由某一方提出。</li>
        <li>中時、民視只取得被 Yahoo 轉載的文章；各家的社論專區不在範圍內。</li>
        <li>只分析文字，不含影音、圖片與節目內容。</li>
        <li>網站只顯示標題、連結、分數與 AI 寫的簡短重點與理由，不轉載新聞全文；全文僅保存在私有資料庫供分析與驗證。</li>
      </ul>

      <h2>8. 隱私</h2>
      <p>
        本站不使用 cookie，也沒有第三方追蹤或廣告程式。為了解網站的使用情形，伺服器只記錄匿名的瀏覽統計：看了哪一頁、從哪個網站連過來（只記網站名稱），
        以及一個每天更換的訪客代碼。這個代碼由 IP 位址與瀏覽器資訊加上每日更換的密鑰雜湊而成，只用來計算當天有幾位不同的訪客，無法還原成 IP，
        也無法跨日追蹤同一位訪客。主機服務（Google Cloud）另有保存 30 天的標準連線紀錄。
      </p>
    </article>
  );
}
