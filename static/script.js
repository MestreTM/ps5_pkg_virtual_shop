document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const tabsContainer = document.getElementById('tabs');
    const contentContainer = document.getElementById('content-area');
    const loading = document.getElementById('loading');
    const blocker = document.getElementById('ps5-blocker');
    const searchBar = document.getElementById('search-bar');
    const shopTitleElement = document.getElementById('shop-title');

    // --- State Management ---
    let masterPkgList = [];
    let categorizedPkgs = {};
    const pageState = {};
    const ITEMS_PER_PAGE = 15;

    // Fetches and applies application settings like the shop title from the backend.
    async function loadSettings() {
        try {
            const response = await fetch('/api/settings');
            if (!response.ok) return;
            const settings = await response.json();
            if (settings.shop_title) {
                shopTitleElement.textContent = settings.shop_title;
                document.title = settings.shop_title;
            }
        } catch (error) {
            console.error('Could not load shop settings:', error);
        }
    }

    // Main entry point for the application.
    async function initializeApp() {
        await loadSettings();
        checkUserAgent();
    }

    // Checks if the client is a PS5. If not, it displays a blocker overlay.
    async function checkUserAgent() {
        try {
            const response = await fetch('/api/check_agent');
            if (!response.ok) throw new Error('Failed to verify user agent');
            const data = await response.json();
            if (data.is_ps5) {
                initializeScanner();
            } else {
                blocker.classList.remove('hidden');
                document.body.classList.add('no-scroll');
            }
        } catch (error) {
            console.error('Error during agent check:', error);
            blocker.classList.remove('hidden');
            document.body.classList.add('no-scroll');
        }
    }

    // Fetches the package list from the server, processes it, and renders the UI.
    async function initializeScanner() {
        loading.classList.remove('hidden');
        tabsContainer.innerHTML = '';
        contentContainer.innerHTML = '';

        try {
            const response = await fetch(`/api/scan`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `Error ${response.status}`);
            }
            masterPkgList = await response.json();
            
            // Group packages by category.
            categorizedPkgs = {};
            for (const pkg of masterPkgList) {
                const category = pkg.category || 'other';
                if (!categorizedPkgs[category]) categorizedPkgs[category] = [];
                categorizedPkgs[category].push(pkg);
            }

            // Sort packages within each category and reset page state.
            for (const category in categorizedPkgs) {
                categorizedPkgs[category].sort((a, b) => (a.title || a.content_id).localeCompare(b.title || b.content_id));
                pageState[category] = 0;
            }

            renderTabsAndPanes();
            setupControllerNavigation();

        } catch (error) {
            console.error('Error while scanning:', error);
            contentContainer.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
        }
    }
    
    // Creates the category tabs and their corresponding content panes.
    function renderTabsAndPanes() {
        tabsContainer.innerHTML = '';
        contentContainer.innerHTML = '';
        tabsContainer.classList.remove('hidden');

        if (masterPkgList.length === 0) {
            contentContainer.innerHTML = '<p style="text-align: center;">No .pkg files found.</p>';
            return;
        }

        const sortedCategoryNames = Object.keys(categorizedPkgs).sort();

        sortedCategoryNames.forEach(categoryName => {
            const tabButton = document.createElement('button');
            tabButton.textContent = categoryName;
            tabButton.dataset.category = categoryName;
            tabsContainer.appendChild(tabButton);

            const tabPane = document.createElement('div');
            tabPane.id = `pane-${categoryName}`;
            tabPane.className = 'content-pane hidden';
            tabPane.innerHTML = `
                <div class="gallery"></div>
                <div class="pagination-controls"></div>
            `;
            contentContainer.appendChild(tabPane);

            tabButton.addEventListener('click', () => activateTab(categoryName));
        });

        // Activate the first tab by default.
        if (sortedCategoryNames.length > 0) {
            activateTab(sortedCategoryNames[0]);
        }
    }

    // Handles tab switching logic, showing the correct content pane.
    function activateTab(categoryName) {
        tabsContainer.querySelectorAll('button').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.category === categoryName);
        });

        contentContainer.querySelectorAll('.content-pane').forEach(pane => {
            pane.classList.toggle('hidden', pane.id !== `pane-${categoryName}`);
        });

        renderPageForCategory(categoryName, pageState[categoryName]);
    }
    
    // Renders the content for a specific category and page number, including pagination.
    function renderPageForCategory(categoryName, pageIndex) {
        const pkgs = categorizedPkgs[categoryName];
        if (!pkgs) return;

        const pane = document.getElementById(`pane-${categoryName}`);
        const gallery = pane.querySelector('.gallery');
        const paginationControls = pane.querySelector('.pagination-controls');

        gallery.innerHTML = '';
        pageState[categoryName] = pageIndex;

        const totalPages = Math.ceil(pkgs.length / ITEMS_PER_PAGE);
        const start = pageIndex * ITEMS_PER_PAGE;
        const pageItems = pkgs.slice(start, start + ITEMS_PER_PAGE);

        pageItems.forEach(pkg => renderPkgCard(pkg, gallery));

        // Render pagination controls if there's more than one page.
        if (totalPages > 1) {
            paginationControls.innerHTML = `
                <button class="prev-btn">Previous</button>
                <span class="page-info">Page ${pageIndex + 1} of ${totalPages}</span>
                <button class="next-btn">Next</button>
            `;
            const prevBtn = paginationControls.querySelector('.prev-btn');
            const nextBtn = paginationControls.querySelector('.next-btn');

            prevBtn.disabled = pageIndex === 0;
            nextBtn.disabled = pageIndex >= totalPages - 1;

            prevBtn.onclick = () => renderPageForCategory(categoryName, pageIndex - 1);
            nextBtn.onclick = () => renderPageForCategory(categoryName, pageIndex + 1);
        } else {
            paginationControls.innerHTML = '';
        }
    }
    
    // Renders a flat list of packages for the search results view.
    function renderSearchView(filteredList) {
        tabsContainer.classList.add('hidden');
        contentContainer.innerHTML = '';

        const searchPane = document.createElement('div');
        searchPane.className = 'content-pane';
        const gallery = document.createElement('div');
        gallery.className = 'gallery';
        searchPane.appendChild(gallery);
        contentContainer.appendChild(searchPane);

        if (filteredList.length === 0) {
            gallery.innerHTML = `<p style="text-align: center;">No packages match your search.</p>`;
            return;
        }
        
        filteredList
            .sort((a, b) => (a.title || a.content_id).localeCompare(b.title || b.content_id))
            .forEach(pkg => renderPkgCard(pkg, gallery));
    }

    // Creates and appends the HTML for a single package card.
    function renderPkgCard(pkg, container) {
        const cardButton = document.createElement('button');
        cardButton.className = 'pkg-card'; 
        
        // Construct the full installation URL using the current host.
        const ip = window.location.hostname;
        const port = window.location.port;
        const pkgUrl = `http://${ip}${port ? ':' + port : ''}${pkg.install_url}`;
        
        cardButton.setAttribute('onClick', `installPkg('${pkgUrl}')`);
        cardButton.innerHTML = `
            <div class="img-container">
                ${pkg.image_path ? 
                    `<img class="btn-img" loading="lazy" src="${pkg.image_path}?t=${new Date().getTime()}" alt="${pkg.title || pkg.content_id}">` : 
                    `<span class="no-image">No Image</span>`
                }
            </div>
            <div class="info">
                <p class="title">${pkg.title || pkg.content_id}</p>
                <p class="file-size">${pkg.file_size_str}</p>
            </div>
        `;
        container.appendChild(cardButton);
    }
    
    // Sets up keyboard listeners for L2/R2 to navigate between tabs.
    function setupControllerNavigation() {
        document.addEventListener('keydown', (event) => {
            // Do not interfere when typing in the search bar.
            if (searchBar === document.activeElement) return;

            const tabButtons = Array.from(tabsContainer.querySelectorAll('button'));
            if (tabButtons.length < 2) return;

            const currentIndex = tabButtons.findIndex(btn => btn.classList.contains('active'));
            let nextIndex = -1;

            if (event.keyCode === 118) { // L2
                event.preventDefault();
                nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
            } else if (event.keyCode === 119) { // R2
                event.preventDefault();
                nextIndex = (currentIndex + 1) % tabButtons.length;
            }

            if (nextIndex !== -1) {
                tabButtons[nextIndex].click();
            }
        });
    }

    // Handles live search filtering as the user types.
    searchBar.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase().trim();

        if (searchTerm) {
            const filtered = masterPkgList.filter(pkg => {
                const title = pkg.title || pkg.content_id || '';
                return title.toLowerCase().includes(searchTerm);
            });
            renderSearchView(filtered);
        } else {
            // If search is cleared, restore the tabbed view.
            renderTabsAndPanes();
        }
    });

    // Start the application.
    initializeApp();
});