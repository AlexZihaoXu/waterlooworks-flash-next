import { memo, useMemo, useState, type ReactNode } from 'react';
import {
  Button,
  Card,
  Disclosure,
  Label,
  ProgressBar,
  Typography,
} from '@heroui/react';
import { LuTerminal } from 'react-icons/lu';
import { RefreshLogEntry } from './RefreshLogEntry';
import { RefreshPanel } from './RefreshPanel';
import type { RefreshSnapshot } from '../lib/workspace';
import { useLogFollow } from '../lib/useLogFollow';
import {
  useRefreshEta,
  useRefreshLogVisibility,
} from '../lib/useRefreshPresentation';

// Existing HeroUI console composition; the trigger has no Button styling.
export const RefreshConsole = memo(function RefreshConsole({
  entries,
  status,
  revision,
}: {
  entries: readonly { key: string; content: ReactNode }[];
  status: string;
  revision: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const { ref, following, onScroll, jumpToLatest } = useLogFollow(
    expanded,
    `${revision}:${expanded}`,
  );
  return (
    <Card variant="secondary">
      <Card.Content>
        <Disclosure isExpanded={expanded} onExpandedChange={setExpanded}>
          <Disclosure.Heading>
            <span className="log-heading-layout">
              <Disclosure.Trigger
                aria-label={
                  expanded ? 'Collapse refresh log' : 'Expand refresh log'
                }
              >
                <span className="log-preview">
                  <span className="filter-heading">
                    <LuTerminal aria-hidden="true" />
                    <Typography type="body-xs">
                      Refresh log · {status}
                    </Typography>
                    <Disclosure.Indicator />
                  </span>
                  {!expanded && (
                    <span className="log-preview-lines">
                      {entries.length ? (
                        entries.slice(-3).map((entry) => (
                          <span className="log-preview-line" key={entry.key}>
                            {entry.content}
                          </span>
                        ))
                      ) : (
                        <Typography type="code">
                          Waiting for refresh progress.
                        </Typography>
                      )}
                    </span>
                  )}
                </span>
              </Disclosure.Trigger>
            </span>
          </Disclosure.Heading>
          <Disclosure.Content>
            <Disclosure.Body>
              <div className="stack-small">
                <div className="workspace-toolbar">
                  <Typography type="body-xs" color="muted">
                    {following ? 'Following latest' : 'Scroll paused'}
                  </Typography>
                  {!following && (
                    <Button size="sm" variant="tertiary" onPress={jumpToLatest}>
                      Jump to latest
                    </Button>
                  )}
                </div>
                <div
                  ref={ref}
                  onScroll={onScroll}
                  className="log-window"
                  role="log"
                  aria-label="Refresh log"
                  aria-live="polite"
                  aria-relevant="additions"
                  tabIndex={0}
                >
                  {entries.length ? (
                    entries.map((entry) => (
                      <div className="console-line" key={entry.key}>
                        {entry.content}
                      </div>
                    ))
                  ) : (
                    <Typography type="code">
                      Waiting for refresh progress.
                    </Typography>
                  )}
                </div>
              </div>
            </Disclosure.Body>
          </Disclosure.Content>
        </Disclosure>
      </Card.Content>
    </Card>
  );
});

export function RefreshActivity({
  refresh,
}: {
  refresh: RefreshSnapshot | null;
}) {
  const running = refresh?.status === 'running';
  const runId = refresh?.run_id ?? null;
  const eta = useRefreshEta(
    runId,
    running,
    refresh?.eta_seconds ?? null,
    refresh?.pages_completed ?? 0,
  );
  const visible = useRefreshLogVisibility(runId, running);
  const entries = useMemo(
    () =>
      refresh?.logs.map((log) => ({
        key: `${runId}-${log.sequence}`,
        content: (
          <RefreshLogEntry
            time={new Date(log.at).toLocaleTimeString('en-GB')}
            level={log.level}
            message={log.message}
          />
        ),
      })) ?? [],
    [refresh?.logs, runId],
  );
  if (!refresh) return null;
  return (
    <>
      <RefreshPanel visible={running}>
        <div className="stack-small">
          <ProgressBar
            aria-label="Listing refresh progress"
            value={refresh.pages_completed}
            maxValue={refresh.total_pages ?? 1}
            isIndeterminate={!refresh.total_pages}
          >
            <Label>
              {refresh.stage.replaceAll('_', ' ')} ·{' '}
              {eta === null
                ? 'ETA unavailable'
                : eta === 0
                  ? 'Updating ETA…'
                  : `ETA ~${Math.floor(eta / 60)}m ${eta % 60}s`}
            </Label>
            <ProgressBar.Output />
            <ProgressBar.Track>
              <ProgressBar.Fill />
            </ProgressBar.Track>
          </ProgressBar>
          <Typography type="body-sm" color="muted" role="status">
            {refresh.pages_completed} / {refresh.total_pages ?? 'unknown'} pages
            · {refresh.unique_postings} postings · {refresh.records_received}{' '}
            records · {refresh.duplicates} duplicates
          </Typography>
        </div>
      </RefreshPanel>
      <RefreshPanel visible={visible}>
        <RefreshConsole
          key={runId}
          entries={entries}
          status={`${refresh.status} · ${refresh.stage.replaceAll('_', ' ')}`}
          revision={`${runId}:${refresh.logs.at(-1)?.sequence ?? 0}`}
        />
      </RefreshPanel>
    </>
  );
}
