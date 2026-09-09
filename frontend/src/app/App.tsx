import { useEffect, useMemo, useRef, useState } from 'react';
import { usePayPreferences } from '../lib/usePayPreferences';
import { compensationForSorting, presentSummary } from '../lib/hourlyPay';
import { countryFlagUrl, inferredCountryName } from '../lib/countryInference';
import { salaryTier } from '../lib/salaryTier';
import { AnimatePresence, motion } from 'framer-motion';
import {
  LuDollarSign,
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
  Breadcrumbs,
  Button,
  Card,
  Chip,
  Label,
  ListBox,
  Link,
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
import {
  compensationMetric,
  sortLabels,
  sortPostings,
  type PostingSort,
} from '../lib/sorting';
import { deadlineBadge, parseDeadline } from '../lib/deadlines';
import { useDebouncedValue } from '../lib/useDebouncedValue';
import { groupedOptionRows } from '../lib/optionGroups';
import { FilterOptionList } from './FilterOptionList';
import { BrandMark } from './BrandMark';
import { readBrowseUrl, writeBrowseUrl } from '../lib/browseUrl';

import { jobUrl, readJobId, shouldOpenJob } from '../lib/jobDetail';
import { useJobDetail } from '../lib/useJobDetail';
import { useSimilarJobs } from '../lib/useSimilarJobs';
import { useLiveReducedMotion } from '../lib/useLiveReducedMotion';
import { JobDetailContent } from './JobDetail';

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
  const [jobId, setJobId] = useState(() => readJobId(window.location.search));
  const detail = useJobDetail(
    jobId,
    `${refresh?.run_id ?? ''}:${refresh?.status ?? ''}`,
  );
  const similarJobs = useSimilarJobs(
    detail,
    `${refresh?.run_id ?? ''}:${refresh?.status ?? ''}`,
  );
  const detailOpen = jobId !== null;
  const returnTarget = useRef<{
    element: HTMLAnchorElement;
    id: string;
    scroll: number;
    windowScroll: number;
  } | null>(null);
  const detailHeading = useRef<HTMLDivElement>(null);
  const previousBrowse = useRef(JSON.stringify(initialView));
  const navigateJob = (id: string | null) => {
    window.history.pushState(
      window.history.state,
      '',
      jobUrl(window.location.href, id),
    );
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  const replaceDetailJob = (id: string) => {
    window.history.replaceState(
      { ...window.history.state, jobEntry: true },
      '',
      jobUrl(window.location.href, id),
    );
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  const backToPostings = () => {
    if (window.history.state?.jobEntry) window.history.back();
    else navigateJob(null);
  };
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
  const payMetric = compensationMetric(sort);
  useEffect(() => {
    const restore = () => {
      const next = readBrowseUrl(window.location.search);
      setJobId(readJobId(window.location.search));
      if (previousBrowse.current === JSON.stringify(next)) return;
      previousBrowse.current = JSON.stringify(next);
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
  const needsTierFx =
    state.status === 'ready' &&
    state.postings.some((posting) =>
      posting.compensation_sort?.source_rates.some(
        (rate) => rate.currency !== null && rate.currency !== 'CAD',
      ),
    );
  const { preferences, setPreferences, fx, fxStatus } =
    usePayPreferences(needsTierFx);
  const connected = state.status === 'ready';
  const paySortAvailable =
    state.status === 'ready' && state.compensation_sort_available;
  const loading = state.status === 'loading';
  const reducedMotion = useLiveReducedMotion();
  const transition = {
    duration: reducedMotion ? 0 : 0.18,
    ease: 'easeInOut' as const,
  };
  const [narrow, setNarrow] = useState(() => window.innerWidth <= 896);
  useEffect(() => {
    const resize = () => setNarrow(window.innerWidth <= 896);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(
      () => {
        if (detailOpen)
          detailHeading.current
            ?.querySelector<HTMLElement>('[data-detail-title]')
            ?.focus({ preventScroll: true });
        else {
          const saved = window.history.state?.jobBrowsePosition as unknown;
          const position =
            returnTarget.current ??
            (typeof saved === 'object' &&
            saved !== null &&
            'id' in saved &&
            typeof saved.id === 'string' &&
            'scroll' in saved &&
            typeof saved.scroll === 'number' &&
            Number.isFinite(saved.scroll) &&
            'windowScroll' in saved &&
            typeof saved.windowScroll === 'number' &&
            Number.isFinite(saved.windowScroll)
              ? {
                  id: saved.id,
                  scroll: saved.scroll,
                  windowScroll: saved.windowScroll,
                }
              : null);
          if (!position) return;
          const { scroll, windowScroll } = position;
          const element = document.getElementById(`job-link-${position.id}`);
          element?.focus({ preventScroll: true });
          if (resultsScroll.current) resultsScroll.current.scrollTop = scroll;
          window.scrollTo({ top: windowScroll, behavior: 'instant' });
        }
      },
      reducedMotion ? 0 : 180,
    );
    return () => window.clearTimeout(timer);
  }, [detailOpen, reducedMotion, connected]);
  const postings = state.status === 'ready' ? state.postings : NO_POSTINGS;
  const presentations = useMemo(
    () =>
      new Map(
        postings.map((posting) => [
          posting.posting_id,
          {
            ...presentSummary(posting.compensation_sort, preferences, fx),
            tier: salaryTier(
              posting.compensation_sort?.source_rates ?? [],
              fx,
              posting.compensation_sort?.needs_review,
            ),
          },
        ]),
      ),
    [postings, preferences, fx],
  );
  const presentedPostings = useMemo(
    () =>
      postings.map((posting) => ({
        ...posting,
        compensation_sort: compensationForSorting(
          posting.compensation_sort,
          preferences,
          fx,
        ),
      })),
    [postings, preferences, fx],
  );
  const options = useMemo(
    () => ({
      location: filterOptions(postings, 'location'),
      country: filterOptions(postings, 'country'),
      organization: filterOptions(postings, 'organization'),
      targeted_disciplines: filterOptions(postings, 'targeted_disciplines'),
    }),
    [postings],
  );
  const countryFlags = useMemo(
    () =>
      new Map(
        postings.flatMap((posting) => {
          const name = inferredCountryName(posting.country_inference);
          const url = name
            ? countryFlagUrl(posting.country_inference.country_code)
            : undefined;
          return name && url ? [[name, url] as const] : [];
        }),
      ),
    [postings],
  );
  // Preserve URL selections even if absent from the current dataset: these
  // should yield no matches, not silently broaden a shared search.
  const activeFilters = filters;
  const pickerRows = useMemo(
    () =>
      groupedOptionRows(
        picker ? options[picker] : [],
        picker ?? '',
        pickerQuery,
      ),
    [picker, options, pickerQuery],
  );
  const pickerOptionCount = pickerRows.filter(
    (row) => row.kind === 'option',
  ).length;
  const filtered = useMemo(
    () =>
      sortPostings(
        filterPostings(presentedPostings, appliedQuery, activeFilters),
        sort,
        now,
      ),
    [presentedPostings, appliedQuery, activeFilters, sort, now],
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
    previousBrowse.current = JSON.stringify(
      readBrowseUrl(window.location.search),
    );
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
    if (readJobId(window.location.search) !== null) return;
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
                      {pickerOptionCount} options · {draft.length} selected.
                      Select one or more.
                    </Typography>
                    <FilterOptionList
                      key={`${picker}:${pickerQuery}`}
                      rows={pickerRows}
                      flags={picker === 'country' ? countryFlags : undefined}
                      label={`${filterLabels[picker]} options`}
                      draft={draft}
                      setDraft={setDraft}
                    />
                  </div>
                </Modal.Body>
                <Modal.Footer>
                  <div
                    className="inline-layout"
                    style={{ justifyContent: 'flex-end', width: '100%' }}
                  >
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
                  </div>
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
        <Alert status="danger">
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

      <motion.div
        className="browser-layout"
        initial={false}
        animate={{
          gridTemplateColumns: narrow
            ? 'minmax(0, 1fr)'
            : detailOpen
              ? '0rem minmax(0, 1fr)'
              : '16rem minmax(0, 1fr)',
          columnGap: detailOpen ? '0rem' : '1.5rem',
        }}
        transition={transition}
      >
        <motion.div
          className="browse-search stack"
          initial={false}
          inert={detailOpen}
          aria-hidden={detailOpen}
          animate={{
            opacity: detailOpen ? 0 : 1,
            visibility: detailOpen ? 'hidden' : 'visible',
          }}
          transition={{
            ...transition,
            visibility: { delay: detailOpen && !reducedMotion ? 0.18 : 0 },
          }}
        >
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
            aria-label="Search titles, companies, locations, and posting IDs"
            value={query}
            onChange={(value) => {
              setQuery(value);
              setPagination({ page: 1, query: value });
            }}
            isDisabled={!connected}
          >
            <SearchField.Group>
              <SearchField.SearchIcon />
              <SearchField.Input placeholder="Search title, company, location, or ID…" />
              <SearchField.ClearButton />
            </SearchField.Group>
          </SearchField>
        </motion.div>
        <motion.aside
          className="filter-sidebar"
          initial={false}
          aria-label="Filters"
          inert={detailOpen}
          aria-hidden={detailOpen}
          animate={{
            opacity: detailOpen ? 0 : 1,
            x: detailOpen ? -10 : 0,
            visibility: detailOpen ? 'hidden' : 'visible',
            ...(narrow
              ? {
                  height: detailOpen ? 0 : 'auto',
                  paddingTop: detailOpen ? 0 : 4,
                  paddingBottom: detailOpen ? 0 : 12,
                }
              : {}),
          }}
          transition={{
            ...transition,
            visibility: { delay: detailOpen && !reducedMotion ? 0.18 : 0 },
          }}
        >
          <ScrollShadow
            hideScrollBar
            tabIndex={0}
            aria-label="Filters and options"
          >
            <div className="sidebar-content">
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
                      <div
                        className={`filter-slot filter-slot-${key}`}
                        key={key}
                      >
                        <div className="stack-small">
                          <Typography
                            type={compact ? 'body-xs' : 'body-sm'}
                            weight={
                              activeFilters[key].length ? 'medium' : 'normal'
                            }
                            color={
                              activeFilters[key].length ? 'default' : 'muted'
                            }
                          >
                            {filterLabels[key]}
                          </Typography>
                          <Button
                            fullWidth
                            variant={
                              activeFilters[key].length
                                ? 'secondary'
                                : 'tertiary'
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
                                  activeFilters[key].length
                                    ? 'default'
                                    : 'muted'
                                }
                                weight={
                                  activeFilters[key].length
                                    ? 'medium'
                                    : 'normal'
                                }
                              >
                                {compact
                                  ? activeFilters[key].length === 0
                                    ? 'All'
                                    : String(activeFilters[key].length)
                                  : activeFilters[key].length === 0
                                    ? `All ${key === 'country' ? 'countries' : key === 'location' ? 'cities' : key === 'targeted_disciplines' ? 'targeted disciplines' : 'organizations'}`
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
                          {key === 'country' &&
                            options.country.length === 0 && (
                              <Typography type="body-xs" color="muted">
                                No current country predictions yet.
                              </Typography>
                            )}
                        </div>
                      </div>
                    ))}
                  </div>
                </Card.Content>
              </Card>
              <Card>
                <Card.Header>
                  <Card.Title>Options</Card.Title>
                </Card.Header>
                <Separator />
                <Card.Content>
                  <div className="stack">
                    <Select
                      fullWidth
                      value={preferences.format}
                      onChange={(value) => {
                        if (value === 'hour' || value === 'month') {
                          setPreferences({ ...preferences, format: value });
                          setPage(1);
                        }
                      }}
                    >
                      <Label>Compensation format</Label>
                      <Select.Trigger>
                        <Select.Value />
                        <Select.Indicator />
                      </Select.Trigger>
                      <Select.Popover>
                        <ListBox>
                          <ListBox.Item id="hour" textValue="Hourly">
                            Hourly
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                          <ListBox.Item id="month" textValue="Monthly">
                            Monthly
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        </ListBox>
                      </Select.Popover>
                    </Select>
                    <Select
                      fullWidth
                      value={preferences.currency}
                      onChange={(value) => {
                        if (
                          value === 'original' ||
                          value === 'CAD' ||
                          value === 'USD'
                        ) {
                          setPreferences({ ...preferences, currency: value });
                          setPage(1);
                        }
                      }}
                    >
                      <Label>Currency</Label>
                      <Select.Trigger>
                        <Select.Value />
                        <Select.Indicator />
                      </Select.Trigger>
                      <Select.Popover>
                        <ListBox>
                          <ListBox.Item
                            id="original"
                            textValue="Original currency"
                          >
                            Original currency
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                          <ListBox.Item id="CAD" textValue="Approx. CAD">
                            Approx. CAD
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                          <ListBox.Item id="USD" textValue="Approx. USD">
                            Approx. USD
                            <ListBox.ItemIndicator />
                          </ListBox.Item>
                        </ListBox>
                      </Select.Popover>
                    </Select>
                    {preferences.currency !== 'original' && (
                      <Typography type="body-xs" color="muted">
                        {fxStatus === 'loading'
                          ? 'Loading reference rates…'
                          : fxStatus === 'unavailable'
                            ? 'FX unavailable; showing original currencies.'
                            : fx
                              ? `Bank of Canada · ${fx.as_of} · approximate conversion`
                              : 'Reference rates not loaded.'}
                      </Typography>
                    )}
                    <Typography type="body-xs" color="muted">
                      * Currency assumed to be CAD; retained after conversion.
                    </Typography>
                  </div>
                </Card.Content>
              </Card>
            </div>
          </ScrollShadow>
        </motion.aside>
        <motion.section
          className="posting-results stack"
          initial={false}
          inert={detailOpen}
          aria-hidden={detailOpen}
          animate={{
            opacity: detailOpen ? 0 : 1,
            visibility: detailOpen ? 'hidden' : 'visible',
          }}
          transition={{
            ...transition,
            visibility: { delay: detailOpen && !reducedMotion ? 0.18 : 0 },
          }}
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
                disabledKeys={
                  paySortAvailable
                    ? []
                    : [
                        'compensation-average',
                        'compensation-minimum',
                        'compensation-maximum',
                      ]
                }
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
          {connected && !paySortAvailable && (
            <Typography type="body-xs" color="muted">
              Compensation sorting requires the updated API. It stays disabled
              until the server can be safely restarted.
            </Typography>
          )}
          {payMetric && paySortAvailable && (
            <Typography type="body-xs" color="muted">
              Highest first within comparable currency / pay-period groups.
              Average = (minimum + maximum) ÷ 2; tiers use outer bounds.
              Weekly/monthly/yearly pay uses stated hours or 40h/week, with 52
              weeks/year. Currency conversion follows your display setting using
              dated FX. Unspecified currencies count as CAD for sorting and
              colors; * marks that assumption. Missing salary sorts as zero;
              unknown units and disputed pay remain unranked. Colors use maximum
              CAD/hour pay.
            </Typography>
          )}
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
                  <AnimatePresence initial={false} mode="sync">
                    <motion.div
                      key={loading ? 'loading' : 'loaded'}
                      className="results-layer"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={transition}
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
                                  {[65, 60, 55, 50, 45].map((width) => (
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
                                      <Link
                                        id={`job-link-${posting.posting_id}`}
                                        href={jobUrl(
                                          window.location.href,
                                          posting.posting_id,
                                        )}
                                        onClick={(event) => {
                                          if (
                                            !shouldOpenJob(event) ||
                                            !(
                                              event.currentTarget instanceof
                                              HTMLAnchorElement
                                            )
                                          )
                                            return;
                                          event.preventDefault();
                                          returnTarget.current = {
                                            element: event.currentTarget,
                                            id: posting.posting_id,
                                            scroll:
                                              resultsScroll.current
                                                ?.scrollTop ?? 0,
                                            windowScroll: window.scrollY,
                                          };
                                          // Store position on the listing entry too, so reload → Back
                                          // restores without depending on an in-memory element ref.
                                          window.history.replaceState(
                                            {
                                              ...window.history.state,
                                              jobBrowsePosition: {
                                                id: posting.posting_id,
                                                scroll:
                                                  returnTarget.current.scroll,
                                                windowScroll:
                                                  returnTarget.current
                                                    .windowScroll,
                                              },
                                            },
                                            '',
                                          );
                                          window.history.pushState(
                                            {
                                              ...window.history.state,
                                              jobEntry: true,
                                            },
                                            '',
                                            jobUrl(
                                              window.location.href,
                                              posting.posting_id,
                                            ),
                                          );
                                          window.dispatchEvent(
                                            new PopStateEvent('popstate'),
                                          );
                                        }}
                                      >
                                        {displayValue(posting.listing.title)}
                                      </Link>
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
                                    <div
                                      className="posting-field"
                                      aria-label="Compensation"
                                    >
                                      <LuDollarSign
                                        size={16}
                                        color="var(--muted)"
                                        aria-hidden="true"
                                      />
                                      <Typography
                                        type="body-sm"
                                        color="muted"
                                        weight="normal"
                                      >
                                        <span
                                          className={`salary-value salary-${presentations.get(posting.posting_id)?.tier ?? 'neutral'}`}
                                        >
                                          {presentations.get(posting.posting_id)
                                            ?.label ?? '--/--'}
                                        </span>
                                      </Typography>
                                    </div>
                                    <div className="location-with-arrangement">
                                      <div className="location-field">
                                        <LuMapPin
                                          size={16}
                                          color="var(--muted)"
                                          role="img"
                                          aria-label="Location"
                                        />
                                        {inferredCountryName(
                                          posting.country_inference,
                                        ) && (
                                          <img
                                            className="country-flag"
                                            src={countryFlagUrl(
                                              posting.country_inference
                                                .country_code,
                                            )}
                                            alt={
                                              posting.country_inference
                                                .country_name ?? ''
                                            }
                                            width={16}
                                          />
                                        )}
                                        <Typography
                                          type="body-sm"
                                          color="muted"
                                        >
                                          {displayValue(posting.location)}
                                        </Typography>
                                      </div>
                                      {posting.work_arrangement && (
                                        <Chip size="sm" variant="soft">
                                          {posting.work_arrangement}
                                        </Chip>
                                      )}
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
        </motion.section>
        <div
          className="detail-layer"
          inert={!detailOpen}
          aria-hidden={!detailOpen}
        >
          <AnimatePresence initial={false}>
            {detail && (
              <motion.section
                key="job-detail"
                className="job-detail"
                aria-label="Job details"
                initial={{ opacity: 0, y: reducedMotion ? 0 : 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={transition}
              >
                <div className="postings-heading detail-heading">
                  <div
                    ref={detailHeading}
                    tabIndex={-1}
                    className="detail-title"
                  >
                    <Breadcrumbs aria-label="Posting navigation">
                      <Breadcrumbs.Item
                        href={jobUrl(window.location.href, null)}
                        onClick={(event) => {
                          if (!shouldOpenJob(event)) return;
                          event.preventDefault();
                          backToPostings();
                        }}
                      >
                        Postings
                      </Breadcrumbs.Item>
                      <Breadcrumbs.Item>
                        <div className="detail-breadcrumb-current">
                          <Typography
                            type="body-sm"
                            weight="medium"
                            render={(props) => <h2 {...props} />}
                            tabIndex={-1}
                            data-detail-title
                          >
                            {detail.status === 'ready'
                              ? displayValue(detail.data.listing.title)
                              : (postings.find(
                                  (posting) => posting.posting_id === jobId,
                                )?.listing.title ??
                                (jobId === 'invalid'
                                  ? 'Invalid posting ID'
                                  : 'Job details'))}
                          </Typography>
                        </div>
                      </Breadcrumbs.Item>
                    </Breadcrumbs>
                  </div>
                </div>
                <div className="detail-scroll" key={jobId}>
                  <AnimatePresence initial={false} mode="sync">
                    <motion.div
                      key={detail.status}
                      className="detail-state"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={transition}
                    >
                      <JobDetailContent
                        state={detail}
                        preferences={preferences}
                        fx={fx}
                        onNavigateJob={replaceDetailJob}
                        similarJobs={similarJobs}
                        postings={postings}
                      />
                    </motion.div>
                  </AnimatePresence>
                </div>
              </motion.section>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </main>
  );
}
