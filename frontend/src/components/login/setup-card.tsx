// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 首次初始化：空库时由第一个设置用户名/口令的人成为超管。
// 旧做法要求先往环境里塞 ST_LOCAL_USERNAME / ST_LOCAL_PASSWORD，没配就只能去
// 容器日志里翻随机口令；这个卡片把这一步搬到界面上。
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod/v4";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import styles from "./login.module.css";

// 与后端 normalize_username / PRIVILEGED_MIN_PASSWORD_LENGTH 对齐。
const USERNAME_RE = /^[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}$/;
const MIN_PASSWORD_LENGTH = 8;

const setupSchema = z
  .object({
    username: z.string().min(1),
    password: z.string().min(1),
    confirm: z.string().min(1),
  })
  .refine((value) => USERNAME_RE.test(value.username.trim()), {
    path: ["username"],
  })
  .refine((value) => value.password.length >= MIN_PASSWORD_LENGTH, {
    path: ["password"],
  })
  .refine((value) => value.password === value.confirm, { path: ["confirm"] });

type SetupForm = z.infer<typeof setupSchema>;

export function SetupCard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const setupAdministrator = useAuthStore((state) => state.setupAdministrator);
  const [failure, setFailure] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { isSubmitting, errors },
  } = useForm<SetupForm>({
    resolver: zodResolver(setupSchema),
    defaultValues: { username: "", password: "", confirm: "" },
  });

  const onSubmit = async (data: SetupForm) => {
    setFailure(null);
    try {
      await setupAdministrator(data.username.trim(), data.password);
      toast.success(t("auth.setup.done"));
      void navigate({ to: "/", replace: true });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : t("auth.setup.failed");
      setFailure(message);
      toast.error(message);
    }
  };

  const fieldError = (name: keyof SetupForm) =>
    errors[name] ? t(`auth.setup.errors.${name}`) : null;

  return (
    <div className={styles.card} data-login-setup-card="1">
      <div className="mb-3 flex items-start gap-2">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-400" aria-hidden />
        <div>
          <h2 className="text-sm font-semibold text-foreground">
            {t("auth.setup.title")}
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {t("auth.setup.description")}
          </p>
        </div>
      </div>

      <form
        noValidate
        className={styles.form}
        onSubmit={handleSubmit(onSubmit, () => setFailure(t("auth.setup.invalid")))}
      >
        <div className={styles.field}>
          <div className={styles.fieldRow}>
            <label htmlFor="setup-username" className={styles.label}>
              {t("auth.setup.username")}
            </label>
          </div>
          <div className={styles.inputWrap}>
            <input
              id="setup-username"
              autoComplete="username"
              placeholder={t("auth.setup.usernamePlaceholder")}
              className={`${styles.input} ${errors.username ? styles.inputInvalid : ""}`}
              {...register("username")}
            />
          </div>
          {fieldError("username") ? (
            <p className={styles.formError} role="alert">
              {fieldError("username")}
            </p>
          ) : null}
        </div>

        <div className={styles.field}>
          <div className={styles.fieldRow}>
            <label htmlFor="setup-password" className={styles.label}>
              {t("auth.setup.password")}
            </label>
          </div>
          <div className={styles.inputWrap}>
            <input
              id="setup-password"
              type="password"
              autoComplete="new-password"
              placeholder={t("auth.setup.passwordPlaceholder", {
                min: MIN_PASSWORD_LENGTH,
              })}
              className={`${styles.input} ${errors.password ? styles.inputInvalid : ""}`}
              {...register("password")}
            />
          </div>
          {fieldError("password") ? (
            <p className={styles.formError} role="alert">
              {fieldError("password")}
            </p>
          ) : null}
        </div>

        <div className={styles.field}>
          <div className={styles.fieldRow}>
            <label htmlFor="setup-confirm" className={styles.label}>
              {t("auth.setup.confirmPassword")}
            </label>
          </div>
          <div className={styles.inputWrap}>
            <input
              id="setup-confirm"
              type="password"
              autoComplete="new-password"
              placeholder={t("auth.setup.confirmPlaceholder")}
              className={`${styles.input} ${errors.confirm ? styles.inputInvalid : ""}`}
              {...register("confirm")}
            />
          </div>
          {fieldError("confirm") ? (
            <p className={styles.formError} role="alert">
              {fieldError("confirm")}
            </p>
          ) : null}
        </div>

        {failure ? (
          <p className={styles.formError} role="alert">
            {failure}
          </p>
        ) : null}

        <div className={styles.formActions}>
          <button type="submit" className={styles.btn} disabled={isSubmitting}>
            <span>{isSubmitting ? t("auth.setup.submitting") : t("auth.setup.submit")}</span>
            <ArrowRight className={styles.btnArrow} strokeWidth={2.4} aria-hidden="true" />
          </button>
        </div>
      </form>
    </div>
  );
}
