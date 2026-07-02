# Homebrew Formula for Empirica
# Documentation: https://docs.brew.sh/Formula-Cookbook
#
# This is the packaging template. The actual formula is in:
#   https://github.com/EmpiricaAI/homebrew-tap/blob/main/empirica.rb
#
# Installation:
#   brew tap empiricaai/tap
#   brew install empirica

class Empirica < Formula
  include Language::Python::Virtualenv

  desc "Epistemic self-assessment framework for AI agents"
  homepage "https://github.com/EmpiricaAI/empirica"
  url "https://files.pythonhosted.org/packages/source/e/empirica/empirica-1.12.10.tar.gz"
  sha256 "9c08a4e30a800830103041c1406696d5e5db3c1a33436b41b6c0af7077a151f6"
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
    assert_match "1.12.10", shell_output("#{bin}/empirica --version")

    # Test that key commands exist
    system "#{bin}/empirica", "session-create", "--help"
    system "#{bin}/empirica", "project-bootstrap", "--help"

    # Test Python import
    system Formula["python@3.11"].opt_bin/"python3", "-c", "from empirica.cli.cli_core import main"
  end
end
