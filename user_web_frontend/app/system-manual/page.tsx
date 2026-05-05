import { redirect } from "next/navigation";

import { getSections } from "../../lib/systemManualContent";

export default function SystemManualIndexPage() {
  const [first] = getSections();
  redirect(first ? `/system-manual/${first}` : "/");
}
