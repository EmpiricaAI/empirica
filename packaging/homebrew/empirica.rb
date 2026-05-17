# Homebrew Formula for Empirica
# Documentation: https://docs.brew.sh/Formula-Cookbook
#
# This is the packaging template. The actual formula is in:
#   https://github.com/Nubaeon/homebrew-tap/blob/main/empirica.rb
#
# Installation:
#   brew tap nubaeon/tap
#   brew install empirica

class Empirica < Formula
  include Language::Python::Virtualenv

  desc "Epistemic self-assessment framework for AI agents"
  homepage "https://github.com/Nubaeon/empirica"
  url "https://files.pythonhosted.org/packages/source/e/empirica/empirica-1.9.8.tar.gz"
  sha256 "761449ea4500537e1a0e61ca10903a4ddcc008a054e5176e4a53fd634c0c8af6"
  license "MIT"

  depends_on "python@3.11"

  # Let pip handle dependencies from pyproject.toml
  # No explicit resources needed - virtualenv_install_with_resources handles this

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      After installation, initialize Empirica in your project:

        cd /path/to/your/git/repo
        empirica project-init

      Or use auto-init when creating your first session:

        empirica session-create --ai-id your-ai-name --auto-init

      For Claude Code integration (status line, hooks, CLAUDE.md):

        empirica setup-claude-code

      Quick start:
        empirica status              # System overview
        empirica onboard --ai-id me  # Full onboarding guide
    EOS
  end

  test do
    # Test that the CLI works
    assert_match "1.9.8", shell_output("#{bin}/empirica --version")

    # Test that key commands exist
    system "#{bin}/empirica", "session-create", "--help"
    system "#{bin}/empirica", "project-bootstrap", "--help"

    # Test Python import
    system Formula["python@3.11"].opt_bin/"python3", "-c", "from empirica.cli.cli_core import main"
  end
end
