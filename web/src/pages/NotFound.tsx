import { Link } from "react-router-dom";
import { usePageTitle } from "../data";
import { Empty } from "../components/States";

export default function NotFound() {
  usePageTitle("找不到這個頁面");
  return (
    <Empty>
      找不到這個頁面。<Link to="/">回到總覽</Link>
    </Empty>
  );
}
