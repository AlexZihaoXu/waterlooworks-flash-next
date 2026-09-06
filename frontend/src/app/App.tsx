import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  LuBuilding2,
  LuMapPin,
  LuHash,
  LuUsers,
  LuCalendarDays,
  LuMoon,
  LuSun,
  LuMonitor,
  LuRefreshCw,
  LuChevronDown,
  LuFilter,
} from 'react-icons/lu';
import {
  Alert,
  Button,
  Card,
  Chip,
  Label,
  ListBox,
  Modal,
  Pagination,
  SearchField,
  ScrollShadow,
  Select,
  Separator,
  Skeleton,
  Toast,
  Tabs,
  toast,
  Typography,
  useTheme,
} from '@heroui/react';
import {
  displayTime,
  displayValue,
  formatCount,
  emptyFilters,
  filterLabels,
  filterOptions,
  filterPostings,
  pageNumbers,
  type FilterKey,
  type PostingFilters,
  type WorkspaceState,
  type Posting,
} from '../lib/workspace';
import { useWorkspace } from '../lib/useWorkspace';
import { RefreshActivity } from './RefreshActivity';
import { sortLabels, sortPostings, type PostingSort } from '../lib/sorting';
import { deadlineBadge, parseDeadline } from '../lib/deadlines';
import { useDebouncedValue } from '../lib/useDebouncedValue';
import { groupedOptionRows } from '../lib/optionGroups';
import { FilterOptionList } from './FilterOptionList';
import { BrandMark } from './BrandMark';
import { readBrowseUrl, writeBrowseUrl } from '../lib/browseUrl';

const PAGE_SIZE = 18;
const NO_POSTINGS: readonly Posting[] = [];
const notice = {
  disconnected: [
    'Backend not connected',
    'Start the local API and reload to load your saved postings.',
  ],
  error: [
    'Workspace unavailable',
    'Could not load saved postings. No refresh was started.',
  ],
} as const;

export function App({ state: suppliedState }: { state?: WorkspaceState }) {
  const remote = useWorkspace(suppliedState === undefined);
  const state = suppliedState ?? remote.state;
  const refresh = suppliedState === undefined ? remote.refresh : null;
  const { theme, setTheme } = useTheme('system');
  const [initialView] = useState(() => readBrowseUrl(window.location.search));
  const navigation = useRef(true);
  const [query, setQuery] = useState(initialView.query);
  const appliedQuery = useDebouncedValue(query, 125);
  const [filters, setFilters] = useState<PostingFilters>(initialView.filters);
  const [pagination, setPagination] = useState({
    page: initialView.page,
    query: initialView.query,
  });
  const page = pagination.query === appliedQuery ? pagination.page : 1;
  const setPage = (page: number) =>
    setPagination({ page, query: appliedQuery });
  const [picker, setPicker] = useState<FilterKey | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState('');
  const [draft, setDraft] = useState<string[]>([]);
  const resultsScroll = useRef<HTMLDivElement>(null);
  const [sort, setSort] = useState<PostingSort>(initialView.sort);
  useEffect(() => {
    const restore = () => {
      const next = readBrowseUrl(window.location.search);
      navigation.current = true;
      setQuery(next.query);
      setFilters(next.filters);
      setSort(next.sort);
      setPagination({ page: next.page, query: next.query });
      setPickerOpen(false);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  const [compact, setCompact] = useState(() => window.innerWidth <= 640);
  useEffect(() => {
    const resize = () => setCompact(window.innerWidth <= 640);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const update = () => setNow(Date.now());
    const timer = window.setInterval(update, 60_000);
    document.addEventListener('visibilitychange', update);
    window.addEventListener('focus', update);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', update);
      window.removeEventListener('focus', update);
    };
  }, []);
  const refreshToast = useRef<string | null>(null);
  const connected = state.status === 'ready';
  const loading = state.status === 'loading';
  const reducedMotion = useReducedMotion();
  const postings = state.status === 'ready' ? state.postings : NO_POSTINGS;
  const options = useMemo(
    () => ({
      location: filterOptions(postings, 'location'),
      country: filterOptions(postings, 'country'),
      organization: filterOptions(postings, 'organization'),
    }),
    [postings],
  );
  // Preserve URL selections even if absent from the current dataset: these
  // should yield no matches, not silently broaden a shared search.
  const activeFilters = filters;
  const pickerOptions = useMemo(
    () =>
      (picker ? options[picker] : []).filter((value) =>
        value
          .toLocaleLowerCase()
          .includes(pickerQuery.trim().toLocaleLowerCase()),
      ),
    [picker, options, pickerQuery],
  );
  const pickerRows = useMemo(
    () => groupedOptionRows(pickerOptions),
    [pickerOptions],
  );
  const filtered = useMemo(
    () =>
      sortPostings(
        filterPostings(postings, appliedQuery, activeFilters),
        sort,
        now,
      ),
    [postings, appliedQuery, activeFilters, sort, now],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  useEffect(() => {
    // Never erase incoming URL state while the dataset or debounce is pending.
    if (!connected || query !== appliedQuery) return;
    const target = writeBrowseUrl(window.location.href, {
      query: appliedQuery,
      filters,
      sort,
      page: currentPage,
    });
    const current =
      window.location.pathname + window.location.search + window.location.hash;
    if (target !== current) {
      window.history[navigation.current ? 'replaceState' : 'pushState'](
        window.history.state,
        '',
        target,
      );
    }
    navigation.current = false;
  }, [connected, query, appliedQuery, filters, sort, currentPage]);
  const visible = filtered.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );
  const lastObserved = postings
    .map((posting) => posting.last_seen_at)
    .sort()
    .at(-1);
  const hasFilters = Boolean(
    appliedQuery.trim() ||
    Object.values(activeFilters).some((values) => values.length > 0),
  );
  const emptyText = !connected
    ? 'No posting data loaded'
    : hasFilters
      ? 'No matching postings'
      : 'No stored postings';
  useEffect(() => {
    const node = resultsScroll.current;
    if (node?.scrollTo)
      node.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
    else if (node) node.scrollTop = 0;
  }, [currentPage, appliedQuery, activeFilters, sort, reducedMotion]);
  const refreshing = refresh?.status === 'running';
  const canRefresh =
    suppliedState === undefined &&
    state.status === 'ready' &&
    state.manual_refresh_available &&
    remote.streamConnected &&
    !remote.requesting;

  return (
    <main className="workspace">
      <Toast.Provider placement="top" maxVisibleToasts={1} />
      <Modal.Backdrop isOpen={pickerOpen} onOpenChange={setPickerOpen}>
        <Modal.Container size="lg" placement="center" scroll="inside">
          <Modal.Dialog>
            <Modal.CloseTrigger />
            <Modal.Header>
              <Modal.Heading>
                {picker ? filterLabels[picker] : ''} filter
              </Modal.Heading>
            </Modal.Header>
            {picker && (
              <>
                <Modal.Body>
                  <div className="stack">
                    <SearchField
                      autoFocus
                      aria-label={`Search ${filterLabels[picker].toLowerCase()} options`}
                      value={pickerQuery}
                      onChange={setPickerQuery}
                    >
                      <SearchField.Group>
                        <SearchField.SearchIcon />
                        <SearchField.Input placeholder="Search options" />
                        <SearchField.ClearButton />
                      </SearchField.Group>
                    </SearchField>
                    <Typography type="body-sm" color="muted" role="status">
                      {pickerOptions.length} options · {draft.length} selected.
                      Select one or more.
                    </Typography>
                    <FilterOptionList
                      key={`${picker}:${pickerQuery}`}
                      rows={pickerRows}
                      label={`${filterLabels[picker]} options`}
                      draft={draft}
                      setDraft={setDraft}
                    />
                  </div>
                </Modal.Body>
                <Modal.Footer>
                  <Button variant="tertiary" onPress={() => setDraft([])}>
                    Clear selection
                  </Button>
                  <Button variant="secondary" slot="close">
                    Cancel
                  </Button>
                  <Button
                    onPress={() => {
                      setFilters((previous) => ({
                        ...previous,
                        [picker]: [...draft],
                      }));
                      setPage(1);
                      setPickerOpen(false);
                    }}
                  >
                    Apply filters
                  </Button>
                </Modal.Footer>
              </>
            )}
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
      <header className="workspace-header">
        <div className="stack-small">
          <div className="brand-heading">
            <BrandMark active={refreshing || remote.requesting} />
            <Typography type="h4" role="heading" aria-level={1}>
              WaterlooWorks Flash Next
            </Typography>
          </div>
          <Typography type="body-xs" color="muted">
            {lastObserved
              ? `Updated ${displayTime(lastObserved)}`
              : loading
                ? '\u00a0'
                : 'Find your next role.'}
          </Typography>
        </div>
        <div className="stack-small">
          <div className="inline-layout">
            <Tabs
              selectedKey={theme}
              onSelectionChange={(key) => {
                if (key === 'system' || key === 'light' || key === 'dark')
                  setTheme(key);
              }}
            >
              <Tabs.ListContainer>
                <Tabs.List aria-label="Color theme">
                  <Tabs.Tab id="system">
                    <span className="theme-choice">
                      <LuMonitor aria-hidden="true" />
                      System
                    </span>
                    <Tabs.Indicator />
                  </Tabs.Tab>
                  <Tabs.Tab id="light">
                    <span className="theme-choice">
                      <LuSun aria-hidden="true" />
                      Light
                    </span>
                    <Tabs.Indicator />
                  </Tabs.Tab>
                  <Tabs.Tab id="dark">
                    <span className="theme-choice">
                      <LuMoon aria-hidden="true" />
                      Dark
                    </span>
                    <Tabs.Indicator />
                  </Tabs.Tab>
                </Tabs.List>
              </Tabs.ListContainer>
            </Tabs>
          </div>
          {connected &&
            state.status === 'ready' &&
            !state.manual_refresh_available && (
              <Typography type="body-xs" color="muted">
                Manual refresh is disabled on this server.
              </Typography>
            )}
        </div>
      </header>

      {(state.status === 'disconnected' || state.status === 'error') && (
        <Alert status={state.status === 'error' ? 'danger' : 'accent'}>
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>{notice[state.status][0]}</Alert.Title>
            <Alert.Description>{notice[state.status][1]}</Alert.Description>
          </Alert.Content>
        </Alert>
      )}
      {remote.actionError && (
        <Alert status="warning">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>Refresh notice</Alert.Title>
            <Alert.Description>{remote.actionError}</Alert.Description>
          </Alert.Content>
        </Alert>
      )}
      {connected && remote.streamInterrupted && suppliedState === undefined && (
        <Alert status="warning">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>Live connection interrupted</Alert.Title>
            <Alert.Description>
              Reconnecting to progress updates. Refresh is disabled until the
              connection returns.
            </Alert.Description>
          </Alert.Content>
        </Alert>
      )}
      <RefreshActivity refresh={refresh} />

      <div className="browser-layout">
        <div className="browse-search stack">
          <div className="postings-heading">
            <Typography type="h2">Postings</Typography>
            <Button
              size="sm"
              variant={
                refreshing || remote.requesting ? 'secondary' : 'tertiary'
              }
              isDisabled={!canRefresh}
              isPending={remote.requesting}
              onPress={async () => {
                const result = await remote.manualRefresh();
                if (result === 'cooldown' || result === 'running') {
                  if (refreshToast.current) toast.close(refreshToast.current);
                  refreshToast.current = toast.warning(
                    result === 'running'
                      ? 'Refresh already running'
                      : 'Refresh is on cooldown',
                    {
                      description:
                        result === 'running'
                          ? 'Wait for the current refresh to finish.'
                          : 'Please wait before refreshing again.',
                    },
                  );
                }
              }}
            >
              <LuRefreshCw
                aria-hidden="true"
                color={
                  refreshing || remote.requesting
                    ? 'currentColor'
                    : 'var(--muted)'
                }
              />
              <Typography
                type="body-sm"
                color={refreshing || remote.requesting ? 'default' : 'muted'}
              >
                {refreshing ? 'Refreshing…' : 'Refresh postings'}
              </Typography>
            </Button>
          </div>
          <SearchField
            aria-label="Search titles"
            value={query}
            onChange={(value) => {
              setQuery(value);
              setPagination({ page: 1, query: value });
            }}
            isDisabled={!connected}
          >
            <SearchField.Group>
              <SearchField.SearchIcon />
              <SearchField.Input placeholder="What role are you looking for?" />
              <SearchField.ClearButton />
            </SearchField.Group>
          </SearchField>
        </div>
        <aside className="filter-sidebar" aria-label="Filters">
          <Card>
            <Card.Header>
              <div className="workspace-toolbar">
                <div className="filter-heading">
                  <LuFilter aria-hidden="true" />
                  <Typography type="h6" role="heading" aria-level={2}>
                    Filters
                  </Typography>
                </div>
                <Button
                  size="sm"
                  variant="tertiary"
                  aria-label="Clear filters"
                  isDisabled={
                    !Object.values(activeFilters).some(
                      (values) => values.length > 0,
                    )
                  }
                  onPress={() => {
                    setFilters(emptyFilters);
                    setPage(1);
                  }}
                >
                  Clear
                </Button>
              </div>
            </Card.Header>
            <Separator />
            <Card.Content>
              <div className="filter-grid">
                {(Object.keys(filterLabels) as FilterKey[]).map((key) => (
                  <div className={`filter-slot filter-slot-${key}`} key={key}>
                    <div className="stack-small">
                      <Typography
                        type={compact ? 'body-xs' : 'body-sm'}
                        weight={activeFilters[key].length ? 'medium' : 'normal'}
                        color={activeFilters[key].length ? 'default' : 'muted'}
                      >
                        {filterLabels[key]}
                      </Typography>
                      <Button
                        fullWidth
                        variant={
                          activeFilters[key].length ? 'secondary' : 'tertiary'
                        }
                        aria-label={`${filterLabels[key]} filter`}
                        aria-description={
                          activeFilters[key].length === 0
                            ? 'All options'
                            : `${activeFilters[key].length} selected`
                        }
                        aria-haspopup="dialog"
                        isDisabled={
                          !connected ||
                          (options[key].length === 0 &&
                            activeFilters[key].length === 0)
                        }
                        onPress={() => {
                          setDraft([...activeFilters[key]]);
                          setPickerQuery('');
                          setPicker(key);
                          setPickerOpen(true);
                        }}
                      >
                        <span className="filter-trigger-content">
                          <Typography
                            type="body-sm"
                            truncate
                            color={
                              activeFilters[key].length ? 'default' : 'muted'
                            }
                            weight={
                              activeFilters[key].length ? 'medium' : 'normal'
                            }
                          >
                            {compact
                              ? activeFilters[key].length === 0
                                ? 'All'
                                : String(activeFilters[key].length)
                              : activeFilters[key].length === 0
                                ? `All ${key === 'country' ? 'countries' : key === 'location' ? 'cities' : 'organizations'}`
                                : activeFilters[key].length === 1
                                  ? activeFilters[key][0]
                                  : `${activeFilters[key].length} selected`}
                          </Typography>
                          <LuChevronDown
                            aria-hidden="true"
                            color={
                              activeFilters[key].length
                                ? 'currentColor'
                                : 'var(--muted)'
                            }
                          />
                        </span>
                      </Button>
                      {key === 'country' && options.country.length === 0 && (
                        <Typography type="body-xs" color="muted">
                          Country data is not verified yet.
                        </Typography>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Card.Content>
          </Card>
        </aside>
        <section
          className="posting-results stack"
          aria-label="Posting browser"
          aria-busy={loading}
        >
          <div className="workspace-toolbar">
            <Typography type="body-sm" color="muted" role="status">
              {connected
                ? `${filtered.length.toLocaleString('en-CA')} ${hasFilters ? (filtered.length === 1 ? 'result' : 'results') : filtered.length === 1 ? 'posting' : 'postings'} · Page ${currentPage}/${pages} · Showing ${visible.length} on this page`
                : loading
                  ? 'Loading saved postings…'
                  : 'Waiting for saved postings'}
            </Typography>
            {hasFilters && (
              <Button
                size="sm"
                variant="tertiary"
                onPress={() => {
                  setQuery('');
                  setFilters(emptyFilters);
                  setPage(1);
                }}
              >
                Clear search and filters
              </Button>
            )}
            <div className="sort-control">
              <Select
                fullWidth
                value={sort}
                isDisabled={!connected}
                onChange={(value) => {
                  if (
                    typeof value === 'string' &&
                    Object.hasOwn(sortLabels, value)
                  ) {
                    setSort(value as PostingSort);
                    setPage(1);
                  }
                }}
              >
                <div className="sort-layout">
                  <Label>Sort by</Label>
                  <Select.Trigger>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                </div>
                <Select.Popover>
                  <ListBox>
                    {(
                      Object.entries(sortLabels) as [PostingSort, string][]
                    ).map(([key, label]) => (
                      <ListBox.Item key={key} id={key} textValue={label}>
                        {label}
                        <ListBox.ItemIndicator />
                      </ListBox.Item>
                    ))}
                  </ListBox>
                </Select.Popover>
              </Select>
            </div>
          </div>
          <div className="results-scroll">
            <ScrollShadow
              size={24}
              ref={resultsScroll}
              tabIndex={0}
              role="region"
              aria-label="Posting cards"
              hideScrollBar
            >
              <div className="results-scroll-content">
                <div className="results-transition">
                  <AnimatePresence initial={false}>
                    <motion.div
                      key={loading ? 'loading' : 'loaded'}
                      className="results-layer"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: reducedMotion ? 0 : 0.18 }}
                    >
                      {loading ? (
                        <div
                          className="posting-grid"
                          aria-hidden="true"
                          data-loading-placeholder="true"
                        >
                          {Array.from({ length: PAGE_SIZE }, (_, index) => (
                            <Card key={index} data-posting-skeleton="true">
                              <Card.Header>
                                <div className="posting-title">
                                  <div className="skeleton-title-block">
                                    <Skeleton animationType="none">
                                      <div className="skeleton-title-space" />
                                    </Skeleton>
                                    <div className="skeleton-short-title">
                                      <Skeleton animationType="none">
                                        <div className="skeleton-title-space" />
                                      </Skeleton>
                                    </div>
                                  </div>
                                </div>
                                <div className="posting-field">
                                  <Skeleton animationType="none">
                                    <div className="skeleton-icon-space" />
                                  </Skeleton>
                                  <div className="skeleton-text-line skeleton-company-line">
                                    <Skeleton animationType="none">
                                      <div className="skeleton-line-space" />
                                    </Skeleton>
                                  </div>
                                </div>
                                <div className="posting-identity">
                                  <div className="posting-field">
                                    <Skeleton animationType="none">
                                      <div className="skeleton-icon-space" />
                                    </Skeleton>
                                    <div className="skeleton-text-line skeleton-id-line">
                                      <Skeleton animationType="none">
                                        <div className="skeleton-line-space" />
                                      </Skeleton>
                                    </div>
                                  </div>
                                  <div className="skeleton-text-line skeleton-deadline-line">
                                    <Skeleton animationType="none">
                                      <div className="skeleton-line-space" />
                                    </Skeleton>
                                  </div>
                                </div>
                              </Card.Header>
                              <Separator />
                              <Card.Content>
                                <div className="posting-details">
                                  {[65, 55, 45].map((width) => (
                                    <div className="posting-field" key={width}>
                                      <Skeleton animationType="none">
                                        <div className="skeleton-icon-space" />
                                      </Skeleton>
                                      <div
                                        className="skeleton-text-line"
                                        style={{ width: `${width}%` }}
                                      >
                                        <Skeleton animationType="none">
                                          <div className="skeleton-line-space" />
                                        </Skeleton>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              </Card.Content>
                            </Card>
                          ))}
                        </div>
                      ) : visible.length === 0 ? (
                        <Card variant="secondary">
                          <Card.Header>
                            <Card.Title>{emptyText}</Card.Title>
                            <Card.Description>
                              {connected
                                ? hasFilters
                                  ? 'Try another title or clear your filters.'
                                  : 'Use manual refresh to load postings when it is enabled.'
                                : 'Connect the local API to browse your postings.'}
                            </Card.Description>
                          </Card.Header>
                        </Card>
                      ) : (
                        <div className="posting-grid">
                          {visible.map((posting) => {
                            const deadline = parseDeadline(
                              posting.listing.deadline,
                            );
                            const badge = deadlineBadge(deadline, now);
                            return (
                              <Card
                                key={posting.posting_id}
                                role="article"
                                aria-labelledby={`posting-${posting.posting_id}`}
                              >
                                <Card.Header>
                                  <div className="posting-title">
                                    <Typography
                                      type="h5"
                                      weight="semibold"
                                      role="heading"
                                      aria-level={3}
                                      id={`posting-${posting.posting_id}`}
                                    >
                                      {displayValue(posting.listing.title)}
                                    </Typography>
                                  </div>
                                  <div className="posting-field">
                                    <LuBuilding2
                                      size={16}
                                      color="var(--muted)"
                                      role="img"
                                      aria-label="Company"
                                    />
                                    <Card.Description>
                                      {displayValue(
                                        posting.listing.organization,
                                      )}
                                    </Card.Description>
                                  </div>
                                  <div className="posting-identity">
                                    <div className="posting-field">
                                      <LuHash
                                        size={16}
                                        color="var(--muted)"
                                        role="img"
                                        aria-label="Posting ID"
                                      />
                                      <Typography type="body-sm" color="muted">
                                        {posting.posting_id}
                                      </Typography>
                                    </div>
                                    <div
                                      title={
                                        deadline === null
                                          ? 'Deadline could not be verified'
                                          : `${displayValue(posting.listing.deadline)} Eastern Time`
                                      }
                                    >
                                      {badge.quiet ? (
                                        <Typography
                                          type="body-xs"
                                          color="muted"
                                          weight="normal"
                                        >
                                          {badge.label}
                                        </Typography>
                                      ) : (
                                        <Chip
                                          size="sm"
                                          variant={badge.variant}
                                          color={badge.color}
                                        >
                                          {badge.label}
                                        </Chip>
                                      )}
                                    </div>
                                  </div>
                                </Card.Header>
                                <Separator />
                                <Card.Content>
                                  <div className="posting-details">
                                    <div className="posting-field">
                                      <LuMapPin
                                        size={16}
                                        color="var(--muted)"
                                        role="img"
                                        aria-label="Location"
                                      />
                                      <Typography type="body-sm" color="muted">
                                        {displayValue(posting.location)}
                                      </Typography>
                                    </div>
                                    <div className="posting-field">
                                      <LuUsers
                                        size={16}
                                        color="var(--muted)"
                                        aria-hidden="true"
                                      />
                                      <Typography type="body-sm" color="muted">
                                        {formatCount(
                                          posting.listing.application_count,
                                          'application',
                                        )}
                                      </Typography>
                                    </div>
                                    <div className="posting-field">
                                      <LuCalendarDays
                                        size={16}
                                        color="var(--muted)"
                                        aria-hidden="true"
                                      />
                                      <Typography type="body-sm" color="muted">
                                        {formatCount(
                                          posting.listing.openings,
                                          'opening',
                                        )}
                                      </Typography>
                                    </div>
                                  </div>
                                </Card.Content>
                              </Card>
                            );
                          })}
                        </div>
                      )}
                    </motion.div>
                  </AnimatePresence>
                </div>
              </div>
            </ScrollShadow>
          </div>
          {!loading && (
            <div className="pagination-dock">
              <div className="pagination-panel">
                <div className="horizontal-overflow">
                  <Pagination aria-label="Posting pages" size="sm">
                    <Pagination.Content>
                      <Pagination.Item>
                        <Pagination.Previous
                          aria-label="Previous"
                          isDisabled={!connected || currentPage === 1}
                          onPress={() => setPage(currentPage - 1)}
                        >
                          <Pagination.PreviousIcon />
                          <span className="pagination-word">Previous</span>
                        </Pagination.Previous>
                      </Pagination.Item>
                      {pageNumbers(currentPage, pages, compact).map(
                        (number, index) => (
                          <Pagination.Item key={`${number}-${index}`}>
                            {number === 'ellipsis' ? (
                              <Pagination.Ellipsis />
                            ) : (
                              <Pagination.Link
                                aria-label={`Page ${number}`}
                                isDisabled={!connected || filtered.length === 0}
                                isActive={number === currentPage}
                                onPress={() => setPage(number)}
                              >
                                {number}
                              </Pagination.Link>
                            )}
                          </Pagination.Item>
                        ),
                      )}
                      <Pagination.Item>
                        <Pagination.Next
                          aria-label="Next"
                          isDisabled={!connected || currentPage === pages}
                          onPress={() => setPage(currentPage + 1)}
                        >
                          <span className="pagination-word">Next</span>
                          <Pagination.NextIcon />
                        </Pagination.Next>
                      </Pagination.Item>
                    </Pagination.Content>
                  </Pagination>
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
