import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  activateReportTemplate,
  deleteReportTemplate,
  getSettings,
  listReportTemplates,
  updateSettings,
  uploadReportTemplate,
} from "@/lib/api";
import type { ReportTemplate, Settings } from "@/lib/api";
import { FormSkeleton } from "@/components/ui/loading-skeletons";
import { toast } from "sonner";
import { Upload, Cpu, FileText, Check, Save, Globe, Bot, Server, Sparkles } from "lucide-react";
import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

const PROVIDERS = [
  {
    value: "anthropic",
    label: "Anthropic Claude",
    icon: Sparkles,
    models: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-3-5-20241022"],
  },
  {
    value: "openai",
    label: "OpenAI / Compatible",
    icon: Globe,
    models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "deepseek-chat", "mistral-large"],
  },
  {
    value: "together",
    label: "Together AI",
    icon: Bot,
    models: [
      "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
      "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
      "deepseek-ai/DeepSeek-V3",
      "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
  },
  {
    value: "openrouter",
    label: "OpenRouter",
    icon: Globe,
    models: [
      "mistralai/mistral-7b-instruct:free",
      "meta-llama/llama-3.2-3b-instruct:free",
      "google/gemma-2-9b-it:free",
    ],
  },
  {
    value: "ollama",
    label: "Ollama (Local)",
    icon: Server,
    models: ["llama3.2", "llama3.1", "mistral", "deepseek-coder", "qwen2.5"],
  },
  {
    value: "gemini",
    label: "Google Gemini",
    icon: Sparkles,
    models: ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"],
  },
];

function SettingsPage() {
  const [provider, setProvider] = useState("anthropic");
  const [chatModel, setChatModel] = useState("");
  const [validationModel, setValidationModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [openaiBaseUrl, setOpenaiBaseUrl] = useState("https://api.openai.com/v1");
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState("http://localhost:11434");
  const [autoValidate, setAutoValidate] = useState(true);
  const [autoMitre, setAutoMitre] = useState(true);
  const [anonymize, setAnonymize] = useState(true);
  const queryClient = useQueryClient();

  const { data: settingsData, isLoading: settingsLoading } = useQuery<Settings>({
    queryKey: ["settings"],
    queryFn: getSettings,
    refetchInterval: 30_000,
  });
  const { data: templates = [] } = useQuery<ReportTemplate[]>({
    queryKey: ["report-templates"],
    queryFn: listReportTemplates,
  });
  const templateMutation = useMutation({
    mutationFn: uploadReportTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["report-templates"] });
      toast.success("Report template uploaded");
    },
    onError: (error) => toast.error("Template upload failed", { description: error.message }),
  });

  useEffect(() => {
    if (settingsData) {
      setProvider(settingsData.llm_provider || "anthropic");
      setChatModel(settingsData.chat_model || "");
      setValidationModel(settingsData.validation_model || "");
      setVisionModel(settingsData.vision_model || "");
      setOpenaiBaseUrl(settingsData.openai_base_url || "https://api.openai.com/v1");
      setOllamaBaseUrl(settingsData.ollama_base_url || "http://localhost:11434");
      setAutoValidate(settingsData.auto_validate !== false);
      setAutoMitre(settingsData.auto_mitre_mapping !== false);
      setAnonymize(settingsData.anonymize_evidence !== false);
    }
  }, [settingsData]);

  const saveMutation = useMutation({
    mutationFn: async (data: Partial<Settings>) => {
      return updateSettings(data);
    },
  });

  const handleSave = () => {
    const payload: Record<string, unknown> = {
      llm_provider: provider,
      auto_validate: autoValidate,
      auto_mitre_mapping: autoMitre,
      anonymize_evidence: anonymize,
    };

    if (provider === "anthropic") {
      payload.anthropic_chat_model = chatModel;
      payload.anthropic_validation_model = validationModel;
      payload.anthropic_vision_model = visionModel;
    } else if (provider === "openai") {
      payload.openai_chat_model = chatModel;
      payload.openai_validation_model = validationModel;
      payload.openai_vision_model = visionModel;
      payload.openai_base_url = openaiBaseUrl;
    } else if (provider === "together") {
      payload.together_chat_model = chatModel;
      payload.together_validation_model = validationModel;
      payload.together_vision_model = visionModel;
    } else if (provider === "openrouter") {
      payload.openrouter_chat_model = chatModel;
      payload.openrouter_validation_model = validationModel;
      payload.openrouter_vision_model = visionModel;
    } else if (provider === "ollama") {
      payload.ollama_chat_model = chatModel;
      payload.ollama_validation_model = validationModel;
      payload.ollama_vision_model = visionModel;
      payload.ollama_base_url = ollamaBaseUrl;
    } else if (provider === "gemini") {
      payload.gemini_chat_model = chatModel;
      payload.gemini_validation_model = validationModel;
      payload.gemini_vision_model = visionModel;
    }

    saveMutation.mutate(payload as Partial<Settings>, {
      onSuccess: () => {
        toast.success("Settings saved", {
          description: "Your workspace configuration has been updated.",
        });
      },
      onError: (error) => {
        toast.error("Failed to save settings", {
          description: error instanceof Error ? error.message : "Please try again.",
        });
      },
    });
  };

  const isLoading = settingsLoading;
  const currentProvider = PROVIDERS.find((p) => p.value === provider) || PROVIDERS[0];
  const ProviderIcon = currentProvider.icon;
  const modelOptions = currentProvider.models;

  return (
    <AppShell>
      <PageHeader
        eyebrow="Settings"
        title="Workspace configuration"
        description="Manage report templates, model providers, and validation safeguards for the internal workspace."
      />

      <div className="max-w-5xl space-y-6 px-5 py-7 md:px-8 lg:px-10">
        <Card className="border-border bg-white p-6 shadow-soft">
          <div className="flex items-start gap-3 mb-5">
            <div className="bg-brand-cyan-soft p-2">
              <Upload className="h-4 w-4 text-brand-cyan" />
            </div>
            <div>
              <h2 className="text-base font-semibold">Report templates</h2>
              <p className="text-xs text-muted-foreground">
                DOCX templates used by the report builder.
              </p>
            </div>
          </div>

          <label className="block cursor-pointer border border-dashed border-input p-7 text-center transition-colors hover:border-brand-cyan hover:bg-brand-cyan-soft">
            <input
              type="file"
              className="hidden"
              accept=".docx"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) templateMutation.mutate(file);
                event.target.value = "";
              }}
            />
            <Upload className="h-6 w-6 mx-auto text-brand-cyan mb-2" />
            <div className="text-sm font-medium">Drop a .docx template or click to upload</div>
            <div className="mt-1 text-[11px] tabular-nums text-muted-foreground">Max 10 MB</div>
          </label>

          <div className="mt-4 space-y-2">
            {templates.length === 0 && (
              <div className="border border-dashed border-border p-4 text-center text-xs text-muted-foreground">
                No template uploaded. Reports will use the built-in plain layout.
              </div>
            )}
            {templates.map((t) => (
              <div
                key={t.name}
                className="flex items-center justify-between border border-border bg-[#fafafa] px-3 py-2.5"
              >
                <div className="flex items-center gap-3">
                  <FileText className="h-4 w-4 text-brand-cyan" />
                  <div>
                    <div className="text-sm font-medium">{t.name}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {(t.size_bytes / 1024).toFixed(1)} KB
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {t.is_active ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-verdict-confirmed">
                      <Check className="h-3 w-3" />
                      Active
                    </span>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        await activateReportTemplate(t.id);
                        queryClient.invalidateQueries({ queryKey: ["report-templates"] });
                      }}
                    >
                      Activate
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      await deleteReportTemplate(t.id);
                      queryClient.invalidateQueries({ queryKey: ["report-templates"] });
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="border-border bg-white p-6 shadow-soft">
          <div className="flex items-start gap-3 mb-5">
            <div className="bg-brand-cyan-soft p-2">
              <Cpu className="h-4 w-4 text-brand-cyan" />
            </div>
            <div>
              <h2 className="text-base font-semibold">AI validation model</h2>
              <p className="text-xs text-muted-foreground">
                Configure model selection and validation behaviour.
              </p>
            </div>
          </div>

          {isLoading ? (
            <FormSkeleton fields={3} />
          ) : (
            <>
              <div className="mb-5">
                <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  LLM Provider
                </Label>
                <div className="mt-2 grid grid-cols-2 md:grid-cols-3 gap-2">
                  {PROVIDERS.map((p) => {
                    const Icon = p.icon;
                    const isActive = provider === p.value;
                    return (
                      <button
                        key={p.value}
                        onClick={() => setProvider(p.value)}
                        className={cn(
                          "flex min-h-11 items-center gap-2 rounded-sm border px-3 py-2.5 text-sm font-medium transition-colors",
                          isActive
                            ? "border-brand-navy bg-brand-navy-soft text-brand-navy"
                            : "bg-background border-border text-muted-foreground hover:border-brand-cyan/30 hover:text-foreground",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        <span className="truncate">{p.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-4 pt-4 border-t border-border">
                <div>
                  <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    <ProviderIcon className="h-3 w-3 inline mr-1" />
                    Chat Model
                  </Label>
                  <Select value={chatModel} onValueChange={setChatModel}>
                    <SelectTrigger className="mt-1.5 bg-background">
                      <SelectValue placeholder="Select a model" />
                    </SelectTrigger>
                    <SelectContent>
                      {modelOptions.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {chatModel && !modelOptions.includes(chatModel) && (
                    <Input
                      value={chatModel}
                      onChange={(e) => setChatModel(e.target.value)}
                      placeholder="Enter custom model name"
                      className="mt-2 text-sm"
                    />
                  )}
                </div>

                <div>
                  <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Validation Model
                  </Label>
                  <Select value={validationModel} onValueChange={setValidationModel}>
                    <SelectTrigger className="mt-1.5 bg-background">
                      <SelectValue placeholder="Same as chat" />
                    </SelectTrigger>
                    <SelectContent>
                      {modelOptions.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Vision Model
                  </Label>
                  <Select value={visionModel} onValueChange={setVisionModel}>
                    <SelectTrigger className="mt-1.5 bg-background">
                      <SelectValue placeholder="Same as chat" />
                    </SelectTrigger>
                    <SelectContent>
                      {modelOptions.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {provider === "openai" && (
                  <div>
                    <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      API Base URL
                    </Label>
                    <Input
                      value={openaiBaseUrl}
                      onChange={(e) => setOpenaiBaseUrl(e.target.value)}
                      placeholder="https://api.openai.com/v1"
                      className="mt-1.5 text-sm"
                    />
                    <p className="text-[10px] text-muted-foreground mt-1">
                      Use for OpenAI-compatible endpoints (DeepSeek, Mistral, etc.)
                    </p>
                  </div>
                )}

                {provider === "ollama" && (
                  <div>
                    <Label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Ollama Server URL
                    </Label>
                    <Input
                      value={ollamaBaseUrl}
                      onChange={(e) => setOllamaBaseUrl(e.target.value)}
                      placeholder="http://localhost:11434"
                      className="mt-1.5 text-sm"
                    />
                  </div>
                )}
              </div>

              <div className="mt-4 pt-4 border-t border-border flex flex-wrap gap-2">
                {settingsData?.anthropic_api_key_set && provider !== "anthropic" && (
                  <span className="inline-flex items-center gap-1 rounded bg-verdict-confirmed/10 px-1.5 py-0.5 text-[10px] font-medium text-verdict-confirmed">
                    <Check className="h-3 w-3" />
                    Anthropic key set
                  </span>
                )}
                {settingsData?.openai_api_key_set && provider !== "openai" && (
                  <span className="inline-flex items-center gap-1 rounded bg-verdict-confirmed/10 px-1.5 py-0.5 text-[10px] font-medium text-verdict-confirmed">
                    <Check className="h-3 w-3" />
                    OpenAI key set
                  </span>
                )}
                {settingsData?.gemini_api_key_set && provider !== "gemini" && (
                  <span className="inline-flex items-center gap-1 rounded bg-verdict-confirmed/10 px-1.5 py-0.5 text-[10px] font-medium text-verdict-confirmed">
                    <Check className="h-3 w-3" />
                    Gemini key set
                  </span>
                )}
                {settingsData?.together_api_key_set && provider !== "together" && (
                  <span className="inline-flex items-center gap-1 rounded bg-verdict-confirmed/10 px-1.5 py-0.5 text-[10px] font-medium text-verdict-confirmed">
                    <Check className="h-3 w-3" />
                    Together key set
                  </span>
                )}
                {settingsData?.ollama_available && provider !== "ollama" && (
                  <span className="inline-flex items-center gap-1 rounded bg-verdict-confirmed/10 px-1.5 py-0.5 text-[10px] font-medium text-verdict-confirmed">
                    <Check className="h-3 w-3" />
                    Ollama running
                  </span>
                )}
              </div>

              <div className="mt-5 pt-5 border-t border-border space-y-4">
                <ToggleRow
                  label="Auto-validate on submission"
                  desc="Run AI validation as soon as a finding is created."
                  checked={autoValidate}
                  onChange={setAutoValidate}
                />
                <ToggleRow
                  label="MITRE ATT&CK auto-mapping"
                  desc="Suggest techniques based on evidence content."
                  checked={autoMitre}
                  onChange={setAutoMitre}
                />
                <ToggleRow
                  label="Anonymize evidence in prompts"
                  desc="Strip client identifiers before sending to the model."
                  checked={anonymize}
                  onChange={setAnonymize}
                />
              </div>

              <div className="mt-5 pt-5 border-t border-border flex items-center justify-end">
                <Button onClick={handleSave} disabled={saveMutation.isPending} className="min-w-36">
                  {saveMutation.isPending ? (
                    <span className="h-4 w-4 mr-1.5 inline-block rounded-full border-2 border-white/30 border-t-white animate-spin" />
                  ) : (
                    <Save className="h-4 w-4 mr-1.5" />
                  )}
                  Save settings
                </Button>
              </div>
            </>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{desc}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
