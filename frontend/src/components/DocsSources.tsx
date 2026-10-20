import type { DocsSource } from '../types';
import { MAX_DOCS, hostOf, usableDocs } from '../utils/docs';
import { groupByQuery } from '../utils/evidence';

interface DocsSourcesProps {
  sources: DocsSource[] | null | undefined;
}

/**
 * Library docs Tavily found to ground Nemotron, grouped by the query that
 * found them: title, host and query for each. Only http(s) links.
 */
export const DocsSources = ({ sources }: DocsSourcesProps) => {
  const usable = usableDocs(sources).slice(0, MAX_DOCS);
  if (usable.length === 0) return null;
  return (
    <section className="block" data-testid="docs-sources" aria-labelledby="docs-heading">
      <h3 id="docs-heading" className="block-title">
        Library docs <span className="block-sub">found by Tavily to ground Nemotron</span>
      </h3>
      <div className="block-body">
        <ul className="docs">
          {groupByQuery(usable).map((group) => (
            <li key={group.query} className="docs-group">
              {group.query && (
                <p className="docs-query">
                  <span className="docs-query-key">Query</span> <code>{group.query}</code>
                </p>
              )}
              <ul className="docs-links">
                {group.sources.map((source, index) => {
                  const host = hostOf(source.url);
                  // Name the host once per run of links from the same site.
                  const sameHost = index > 0 && hostOf(group.sources[index - 1].url) === host;
                  return (
                    <li key={source.url}>
                      <a href={source.url} target="_blank" rel="noopener noreferrer">
                        {source.title || host}
                      </a>
                      {!sameHost && (
                        <>
                          {' '}
                          <span className="docs-host">{host}</span>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
};
