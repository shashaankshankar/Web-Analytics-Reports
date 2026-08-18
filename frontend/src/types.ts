export type Role = 'agency_owner' | 'agency_admin' | 'agency_analyst' | 'client_admin' | 'client_viewer';
export type Period = '7d' | '28d' | 'this_month' | 'last_month' | '90d';
export type Status = 'ok' | 'ready' | 'warning' | 'pending' | 'blocked' | 'error' | 'info' | 'neutral';
export interface Company { id: string; companyId?: string; name: string; organizationId?: string; industry?: string; createdAt?: string; }
export interface Website { id: string; websiteId?: string; companyId: string; name?: string; canonicalDomain: string; status?: string; syncHealth?: Status | string; lastSync?: string | null; }
export interface Identity { approved: boolean; identityMode: 'iap' | string; identity: { email: string; role: Role }; }
export interface PortalResource { companyId: string; companyName: string; websiteId: string; canonicalDomain: string; role?: Role; }
export interface PortalResources { resources: PortalResource[]; }
export interface Metric { metric: string; value: number | null; previousValue?: number | null; period?: { start: string; end: string }; unit?: string; }
export interface OverviewData { companyId?: string; websiteId?: string; period?: Period; metrics: Metric[]; annotations?: Annotation[]; [key: string]: unknown; }
export interface AcquisitionRow { channel: string; sessions?: number | null; users?: number | null; conversions?: number | null; conversionRate?: number | null; [key: string]: unknown; }
export interface AcquisitionData { websiteId: string; rows?: AcquisitionRow[]; channels?: AcquisitionRow[]; breakdown?: AcquisitionRow[]; caveats?: string[]; [key: string]: unknown; }
export interface ConversionMetric { metric: string; value: number | null; previousValue?: number | null; [key: string]: unknown; }
export interface ConversionData { websiteId: string; metrics?: ConversionMetric[]; events?: ConversionMetric[]; caveats?: string[]; [key: string]: unknown; }
export interface LandingPage { landingPage: string; sessions?: number | null; users?: number | null; conversions?: number | null; engagementRate?: number | null; [key: string]: unknown; }
export interface LandingPagesData { websiteId: string; pages?: LandingPage[]; rows?: LandingPage[]; caveats?: string[]; [key: string]: unknown; }
export interface HealthCheck { key: string; state: Status; detail: string; [key: string]: unknown; }
export interface HealthData { websiteId: string; state: Status | string; governanceStatus?: string; checks?: HealthCheck[]; sync?: SyncStatus; contract?: { slug: string; version: number; approvalStatus: string }; [key: string]: unknown; }
export interface SyncStatus { websiteId?: string; status: string; quality?: Record<string, unknown>; lastSuccessfulSync?: string | null; lastAttemptedSync?: string | null; error?: string | null; [key: string]: unknown; }
export interface Goal { id?: string; metric: string; target: number; currentValue?: number | null; effectiveFrom?: string; effectiveTo?: string | null; status?: string; }
export interface GoalsData { websiteId: string; goals: Goal[]; approvedMetrics?: string[]; }
export interface Membership { email: string; role: Role; userId?: string; companyId?: string; websiteId?: string; status?: string; }
export interface MembershipData { memberships: Membership[]; }
export interface PortfolioClient { company: Company; website: Website; status?: Status | string; alertCount?: number; onboardingStatus?: string; [key: string]: unknown; }
export interface PortfolioData { clients?: PortfolioClient[]; companies?: Company[]; websites?: Website[]; alerts?: Alert[]; [key: string]: unknown; }
export interface Alert { id?: string; severity: Status | string; title: string; detail?: string; websiteId?: string; createdAt?: string; }
export interface Annotation { date: string; type: string; note: string; }
export type OnboardingStepState = 'pending' | 'in_progress' | 'completed' | 'blocked' | 'deferred';
export interface OnboardingStep { key: string; stepKey?: string; label?: string; status: OnboardingStepState; detail?: string; }
export interface OnboardingWorkflow { id: string; status: 'in_progress' | 'blocked' | 'ready' | 'completed' | 'cancelled'; companyId?: string; websiteId?: string; governanceStatus?: string; consentStatus?: string; steps?: OnboardingStep[]; [key: string]: unknown; }
export interface OnboardingChecklist { workflowId: string; steps: OnboardingStep[]; status?: string; }
export interface OAuthStatus { enabled: boolean; state?: string; detail?: string; [key: string]: unknown; }
