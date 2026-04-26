import MarkdownContent from "../../../components/MarkdownContent";
import {
  getManualContent,
  getSectionChildren,
  getSections,
  slugToLabel,
} from "../../../lib/systemManualContent";

export function generateStaticParams() {
  return getSections().map((section) => ({ section }));
}

export default function SectionPage({ params }: { params: { section: string } }) {
  const { section } = params;
  const intro = getManualContent(`${section}/index`);
  const children = getSectionChildren(section);

  return (
    <>
      <h1 className="sm-page-title">{slugToLabel(section)}</h1>
      {intro && <MarkdownContent content={intro} />}
      {children.map((childSlug) => {
        const content = getManualContent(`${section}/${childSlug}`);
        return (
          <section key={childSlug}>
            <h2 id={childSlug} className="sm-section-title">
              {slugToLabel(childSlug)}
            </h2>
            {content && <MarkdownContent content={content} />}
          </section>
        );
      })}
    </>
  );
}
