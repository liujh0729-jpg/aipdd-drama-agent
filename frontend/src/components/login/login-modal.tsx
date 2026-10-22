// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useReducedMotion } from "@/hooks/use-reduced-motion";
import { useSetupStatus } from "@/lib/queries/admin-users";
import { phoneOtpEntryVisible } from "@/lib/runtime-config";
import { loginModalShowcaseVideo } from "./cinematic/media";
import { LoginCard } from "./login-card";
import styles from "./login.module.css";

type LoginModalProps = {
  open: boolean;
  onClose: () => void;
  /** [aipdd fork delta] 登录成功回调：登录页顶栏 icon 的带意图登录由父层接管去向。 */
  onAuthenticated?: () => void;
};

export function LoginModal({ open, onClose, onAuthenticated }: LoginModalProps) {
  const { t } = useTranslation();
  const reducedMotion = useReducedMotion();
  const otpEntryVisible = phoneOtpEntryVisible();
  // 空库时 LoginCard 里换成初始化表单，这里要同步收起营销标题栏：那 220px
  // 会把提交按钮顶出可点区域（1920×1080 下按钮落在遮罩下面，只有回车能提交）。
  const setupStatus = useSetupStatus();
  const needsSetup = Boolean(setupStatus.data?.data?.needs_setup);

  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className={styles.loginOverlay}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          role="dialog"
          aria-modal="true"
          aria-label={needsSetup ? t("auth.setup.title") : t("auth.login")}
        >
          <motion.div
            className={styles.loginDialog}
            data-setup={needsSetup ? "true" : undefined}
            initial={{ opacity: 0, scale: 0.98, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 10 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            <button
              type="button"
              className={styles.loginClose}
              onClick={onClose}
              aria-label={t("auth.closeLogin")}
            >
              <X strokeWidth={1.8} aria-hidden="true" />
            </button>

            <section className={styles.loginMedia} aria-label={t("auth.modal.showcaseLabel")}>
              <video
                className={styles.loginMediaVideo}
                src={loginModalShowcaseVideo}
                muted
                playsInline
                autoPlay={!reducedMotion}
                loop
                preload="metadata"
                aria-hidden="true"
              />
              <div className={styles.loginMediaShade} aria-hidden="true" />
              <div className={styles.loginMediaCopy}>
                <h2>{t("auth.modal.showcaseTitle")}</h2>
                <span>{t("auth.modal.showcaseDescription")}</span>
              </div>
            </section>

            <section className={styles.loginPanel}>
              <div className={styles.loginPanelInner}>
                {needsSetup ? null : (
                  <header className={styles.loginPanelHeader}>
                    <img
                      className={styles.loginPanelBrand}
                      src="/brand/dramaclaw-wordmark.png"
                      alt="AIPDD 画布"
                    />
                    <h2 className={styles.loginPanelTitle}>{t("auth.modal.title")}</h2>
                    <p className={styles.loginPanelSubtitle}>{t("auth.modal.subtitle")}</p>
                  </header>
                )}

                <LoginCard onAuthenticated={onAuthenticated} />

                {otpEntryVisible && !needsSetup ? (
                  <div className={styles.loginApplyRow}>
                    <span>{t("auth.modal.otpRegistration")}</span>
                  </div>
                ) : null}
              </div>
            </section>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
